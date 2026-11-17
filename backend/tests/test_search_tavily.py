"""The live search provider, driven against a fake transport.

No network, no credentials, no credits: every response below is scripted, so
these run in CI alongside everything else. They cover the failure modes a real
provider actually produces — malformed payloads, empty result sets, duplicate
pages under different tracking URLs, outages and rate limits.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.adapters.search.tavily import MAX_EXCERPT_CHARS, TavilySearchProvider
from app.domain.enums import SearchMode
from app.domain.errors import (
    AdapterError,
    ProviderAuthError,
    ProviderRateLimitedError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)


def result(url: str, **overrides: Any) -> dict[str, Any]:
    body = {
        "url": url,
        "title": "A page",
        "content": "Some retrieved text about workflow orchestration.",
        "score": 0.91,
        "published_date": "2026-03-04",
    }
    body.update(overrides)
    return body


def build(responses: list[Any], **kwargs: Any) -> tuple[TavilySearchProvider, list[httpx.Request]]:
    """A provider whose transport replays a scripted sequence."""
    seen: list[httpx.Request] = []
    queue = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        outcome = queue.pop(0) if queue else httpx.Response(200, json={"results": []})
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    provider = TavilySearchProvider(
        api_key="unit-test-credential-value", max_attempts=kwargs.pop("max_attempts", 2), **kwargs
    )
    provider._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        headers={"Authorization": "Bearer unit-test-credential-value"},
    )
    return provider, seen


class TestSuccessPath:
    async def test_results_are_mapped_with_metadata_preserved(self) -> None:
        provider, _ = build(
            [httpx.Response(200, json={"results": [result("https://example.com/a")]})]
        )
        try:
            results = await provider.search("workflow orchestration", max_results=5)
        finally:
            await provider.aclose()

        assert len(results) == 1
        found = results[0]
        assert found.url == "https://example.com/a"
        assert found.provider == "tavily"
        assert found.is_fixture is False
        assert found.score == pytest.approx(0.91)
        assert found.published_date == "2026-03-04"
        assert found.rank == 0

    async def test_the_cheap_search_depth_is_requested(self) -> None:
        """Basic costs 1 credit against the free allowance; advanced costs 2,
        and nothing in this workflow needs it."""
        import json

        provider, seen = build([httpx.Response(200, json={"results": []})])
        try:
            await provider.search("q", max_results=5)
        finally:
            await provider.aclose()
        payload = json.loads(seen[0].content)
        assert payload["search_depth"] == "basic"
        assert payload["include_raw_content"] is False

    async def test_excerpts_are_capped(self) -> None:
        """Eight unbounded excerpts would dominate the analysis prompt."""
        provider, _ = build(
            [
                httpx.Response(
                    200,
                    json={"results": [result("https://example.com/a", content="x" * 9000)]},
                )
            ]
        )
        try:
            results = await provider.search("q", max_results=5)
        finally:
            await provider.aclose()
        assert len(results[0].excerpt) == MAX_EXCERPT_CHARS

    async def test_the_provider_reports_its_mode(self) -> None:
        provider, _ = build([])
        try:
            assert provider.mode is SearchMode.TAVILY
        finally:
            await provider.aclose()


class TestEmptyResults:
    async def test_an_empty_result_set_is_returned_not_raised(self) -> None:
        """The provider answered and found nothing. That is a finding about
        the query, not a failure, and must not be confused with one."""
        provider, _ = build([httpx.Response(200, json={"results": []})])
        try:
            assert await provider.search("a query with no matches", max_results=5) == []
        finally:
            await provider.aclose()

    async def test_a_response_with_no_results_key_yields_nothing(self) -> None:
        provider, _ = build([httpx.Response(200, json={"query": "q"})])
        try:
            assert await provider.search("q", max_results=5) == []
        finally:
            await provider.aclose()


class TestMalformedResponses:
    @pytest.mark.parametrize(
        "payload",
        [
            {"results": "not-a-list"},
            {"results": [None, 42, "string"]},
            {"results": [{}]},
            {"results": [{"title": "no url"}]},
            {"results": [{"url": ""}]},
            {"results": [{"url": "   "}]},
        ],
    )
    async def test_unusable_entries_are_dropped_not_fatal(self, payload: dict[str, Any]) -> None:
        """One unusable entry costs a source; raising would discard an
        otherwise good result set."""
        provider, _ = build([httpx.Response(200, json=payload)])
        try:
            assert await provider.search("q", max_results=5) == []
        finally:
            await provider.aclose()

    async def test_good_entries_survive_alongside_bad_ones(self) -> None:
        provider, _ = build(
            [
                httpx.Response(
                    200,
                    json={
                        "results": [
                            {"url": ""},
                            result("https://example.com/good"),
                            None,
                        ]
                    },
                )
            ]
        )
        try:
            results = await provider.search("q", max_results=5)
        finally:
            await provider.aclose()
        assert [r.url for r in results] == ["https://example.com/good"]

    async def test_a_non_json_body_is_retried_then_reported(self) -> None:
        provider, seen = build(
            [
                httpx.Response(200, content=b"<html>not json</html>"),
                httpx.Response(200, json={"results": [result("https://example.com/a")]}),
            ]
        )
        try:
            results = await provider.search("q", max_results=5)
        finally:
            await provider.aclose()
        assert len(seen) == 2
        assert len(results) == 1

    async def test_missing_metadata_is_tolerated(self) -> None:
        """A provider omitting score or date is normal, not an error."""
        provider, _ = build(
            [
                httpx.Response(
                    200,
                    json={
                        "results": [
                            {"url": "https://example.com/a", "content": "text"},
                        ]
                    },
                )
            ]
        )
        try:
            results = await provider.search("q", max_results=5)
        finally:
            await provider.aclose()
        assert results[0].score is None
        assert results[0].published_date is None
        # Falls back to the URL rather than inventing a title.
        assert results[0].title == "https://example.com/a"


class TestDuplicateAndUnsafeSources:
    async def test_the_same_page_under_tracking_urls_is_stored_once(self) -> None:
        """Otherwise one page becomes several sources, several citations and
        several entries in the report's source list."""
        provider, _ = build(
            [
                httpx.Response(
                    200,
                    json={
                        "results": [
                            result("https://example.com/a?utm_source=twitter"),
                            result("HTTPS://Example.com/a/#top"),
                            result("https://example.com/a?fbclid=xyz"),
                        ]
                    },
                )
            ]
        )
        try:
            results = await provider.search("q", max_results=5)
        finally:
            await provider.aclose()
        assert [r.url for r in results] == ["https://example.com/a"]

    async def test_unsafe_urls_are_dropped_from_the_result_set(self) -> None:
        provider, _ = build(
            [
                httpx.Response(
                    200,
                    json={
                        "results": [
                            result("http://169.254.169.254/latest/meta-data/"),
                            result("javascript:alert(1)"),
                            result("https://user:pw@example.com/x"),
                            result("https://example.com/ok"),
                        ]
                    },
                )
            ]
        )
        try:
            results = await provider.search("q", max_results=5)
        finally:
            await provider.aclose()
        assert [r.url for r in results] == ["https://example.com/ok"]

    async def test_ranks_are_contiguous_after_dropping(self) -> None:
        """Rank feeds the source label order, so a gap would misalign
        citations."""
        provider, _ = build(
            [
                httpx.Response(
                    200,
                    json={
                        "results": [
                            result("javascript:alert(1)"),
                            result("https://example.com/a"),
                            result("https://example.com/b"),
                        ]
                    },
                )
            ]
        )
        try:
            results = await provider.search("q", max_results=5)
        finally:
            await provider.aclose()
        assert [r.rank for r in results] == [0, 1]


class TestProviderFailures:
    @pytest.mark.parametrize(
        ("status", "expected", "retryable"),
        [
            (401, ProviderAuthError, False),
            (403, ProviderAuthError, False),
            (429, ProviderRateLimitedError, True),
            (408, ProviderTimeoutError, True),
            (500, ProviderUnavailableError, True),
            (503, ProviderUnavailableError, True),
            (400, AdapterError, False),
        ],
    )
    async def test_statuses_map_to_domain_errors(
        self, status: int, expected: type[AdapterError], retryable: bool
    ) -> None:
        provider, _ = build([httpx.Response(status, text="boom")] * 3, max_attempts=1)
        try:
            with pytest.raises(expected) as excinfo:
                await provider.search("q", max_results=5)
        finally:
            await provider.aclose()
        assert excinfo.value.retryable is retryable

    async def test_a_timeout_is_normalised(self) -> None:
        provider, _ = build([httpx.TimeoutException("slow")], max_attempts=1)
        try:
            with pytest.raises(ProviderTimeoutError):
                await provider.search("q", max_results=5)
        finally:
            await provider.aclose()

    async def test_a_connection_failure_is_normalised(self) -> None:
        provider, _ = build([httpx.ConnectError("refused")], max_attempts=1)
        try:
            with pytest.raises(ProviderUnavailableError):
                await provider.search("q", max_results=5)
        finally:
            await provider.aclose()

    async def test_a_failure_never_degrades_into_an_empty_result_set(self) -> None:
        """An empty list would read as "the web has nothing on this", which is
        a different and much worse claim than "retrieval failed"."""
        provider, _ = build([httpx.Response(503)], max_attempts=1)
        try:
            with pytest.raises(ProviderUnavailableError):
                await provider.search("q", max_results=5)
        finally:
            await provider.aclose()

    async def test_an_auth_failure_is_not_retried(self) -> None:
        provider, seen = build([httpx.Response(401)] * 3, max_attempts=3)
        try:
            with pytest.raises(ProviderAuthError):
                await provider.search("q", max_results=5)
        finally:
            await provider.aclose()
        assert len(seen) == 1

    async def test_a_transient_failure_is_retried_then_succeeds(self) -> None:
        provider, seen = build(
            [
                httpx.Response(503),
                httpx.Response(200, json={"results": [result("https://example.com/a")]}),
            ],
            max_attempts=3,
        )
        try:
            results = await provider.search("q", max_results=5)
        finally:
            await provider.aclose()
        assert len(seen) == 2
        assert len(results) == 1


class TestRateLimits:
    async def test_a_rate_limit_carries_the_providers_retry_hint(self) -> None:
        provider, _ = build([httpx.Response(429, headers={"Retry-After": "7"})], max_attempts=1)
        try:
            with pytest.raises(ProviderRateLimitedError) as excinfo:
                await provider.search("q", max_results=5)
        finally:
            await provider.aclose()
        assert excinfo.value.retry_after_seconds == 7.0

    async def test_the_hinted_delay_is_waited_rather_than_guessed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        slept: list[float] = []

        async def fake_sleep(seconds: float) -> None:
            slept.append(seconds)

        monkeypatch.setattr("app.adapters.search.tavily.asyncio.sleep", fake_sleep)
        provider, _ = build(
            [
                httpx.Response(429, headers={"Retry-After": "4"}),
                httpx.Response(200, json={"results": [result("https://example.com/a")]}),
            ],
            max_attempts=2,
        )
        try:
            await provider.search("q", max_results=5)
        finally:
            await provider.aclose()
        assert slept == [4.0]

    async def test_an_absurd_retry_hint_is_capped(self) -> None:
        """A monthly reset hours away should fail fast, not hang a worker."""
        provider, _ = build([httpx.Response(429, headers={"Retry-After": "99999"})], max_attempts=1)
        try:
            with pytest.raises(ProviderRateLimitedError) as excinfo:
                await provider.search("q", max_results=5)
        finally:
            await provider.aclose()
        assert excinfo.value.retry_after_seconds == 60.0

    async def test_a_date_form_hint_is_ignored_rather_than_guessed(self) -> None:
        provider, _ = build(
            [httpx.Response(429, headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"})],
            max_attempts=1,
        )
        try:
            with pytest.raises(ProviderRateLimitedError) as excinfo:
                await provider.search("q", max_results=5)
        finally:
            await provider.aclose()
        assert excinfo.value.retry_after_seconds is None
