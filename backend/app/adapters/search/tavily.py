"""Live web search via Tavily.

Chosen because it returns a content snippet per result, so AgentFlow still
never dereferences a URL. That keeps the SSRF surface **absent** rather than
mitigated, which is the property `docs/security.md` claims (ADR-013).

Retrieved text is untrusted. Nothing here decides whether a page is reliable;
it decides only that a URL is safe to store and that the snippet is text. The
report's claim-support table is where evidence is distinguished from verified
fact, and it says so explicitly.
"""

from __future__ import annotations

import asyncio
import random
from typing import Any

import httpx

from app.adapters.search.base import SearchResult
from app.adapters.search.urls import validate_url
from app.core.logging import get_logger
from app.domain.enums import SearchMode
from app.domain.errors import (
    AdapterError,
    ProviderAuthError,
    ProviderRateLimitedError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    UnsafeUrlError,
)

log = get_logger(__name__)

DEFAULT_BASE_URL = "https://api.tavily.com"
PROVIDER_NAME = "tavily"

#: "basic" costs 1 API credit against the free monthly allowance; "advanced"
#: costs 2. Nothing in this workflow needs advanced retrieval.
DEFAULT_SEARCH_DEPTH = "basic"

#: Tavily caps max_results at 20.
MAX_RESULTS_CEILING = 20

_BACKOFF_BASE_SECONDS = 1.0
_BACKOFF_CAP_SECONDS = 15.0
#: Never wait longer than this on the provider's say-so.
MAX_HONOURED_RETRY_AFTER_SECONDS = 60.0

#: Excerpt length kept per source. Long enough to ground a claim, short
#: enough that eight sources do not dominate the analysis prompt.
MAX_EXCERPT_CHARS = 1200


def _parse_retry_after(headers: httpx.Headers) -> float | None:
    raw = headers.get("retry-after")
    if not raw:
        return None
    try:
        seconds = float(str(raw).strip())
    except ValueError:
        return None  # the HTTP-date form is ignored rather than guessed at
    if seconds <= 0:
        return None
    return min(seconds, MAX_HONOURED_RETRY_AFTER_SECONDS)


def _normalise_status(status: int, *, detail: str, retry_after: float | None) -> AdapterError:
    """Map a Tavily HTTP status onto the shared domain error taxonomy."""
    kwargs: dict[str, Any] = {"status_code": status, "retry_after_seconds": retry_after}
    if status in (401, 403):
        return ProviderAuthError("Tavily rejected the API key. Check SEARCH_API_KEY.", **kwargs)
    if status == 429:
        return ProviderRateLimitedError(
            "Tavily rate-limited the request or the monthly credit allowance is spent.",
            **kwargs,
        )
    if status in (408, 504):
        return ProviderTimeoutError("Tavily did not respond in time.", **kwargs)
    if status >= 500:
        return ProviderUnavailableError(f"Tavily returned HTTP {status}.", **kwargs)
    return AdapterError(f"Tavily returned HTTP {status}: {detail[:200]}", **kwargs)


class TavilySearchProvider:
    """A :class:`~app.adapters.search.base.SearchProvider` backed by Tavily."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        timeout_seconds: float = 20.0,
        max_attempts: int = 3,
        search_depth: str = DEFAULT_SEARCH_DEPTH,
        exclude_domains: list[str] | None = None,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._max_attempts = max(1, max_attempts)
        self._search_depth = search_depth
        self._exclude_domains = exclude_domains or []
        self._client = httpx.AsyncClient(
            timeout=timeout_seconds,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )

    @property
    def mode(self) -> SearchMode:
        return SearchMode.TAVILY

    async def search(self, query: str, *, max_results: int) -> list[SearchResult]:
        """Retrieve ranked results, or raise a domain error.

        A failure is raised rather than degraded into an empty result set: an
        empty list would look like "the web has nothing on this", which is a
        different and much worse claim than "retrieval failed".
        """
        payload = {
            "query": query,
            "max_results": min(max(max_results, 1), MAX_RESULTS_CEILING),
            "search_depth": self._search_depth,
            "include_answer": False,
            "include_raw_content": False,
            "include_images": False,
            "include_published_date": True,
        }
        if self._exclude_domains:
            payload["exclude_domains"] = self._exclude_domains

        body = await self._post_with_retries("/search", payload, query=query)
        return self._to_results(body)

    async def usage(self) -> dict[str, Any]:
        """Report credit usage, for pre-flight quota checks.

        Separate from ``search`` and never called on the research path: its
        own rate limit is far tighter than search's.
        """
        response = await self._client.get(f"{self._base_url}/usage")
        if response.status_code >= 400:
            raise _normalise_status(
                response.status_code,
                detail=response.text,
                retry_after=_parse_retry_after(response.headers),
            )
        payload: dict[str, Any] = response.json()
        return payload

    async def aclose(self) -> None:
        await self._client.aclose()

    # -- internals -------------------------------------------------------

    async def _post_with_retries(
        self, path: str, payload: dict[str, Any], *, query: str
    ) -> dict[str, Any]:
        last: AdapterError | None = None

        for attempt in range(1, self._max_attempts + 1):
            try:
                response = await self._client.post(f"{self._base_url}{path}", json=payload)
            except httpx.TimeoutException as exc:
                last = ProviderTimeoutError(f"Tavily timed out: {exc}")
            except (httpx.ConnectError, httpx.NetworkError, httpx.RemoteProtocolError) as exc:
                last = ProviderUnavailableError(f"Tavily is unreachable: {exc}")
            else:
                if response.status_code < 400:
                    try:
                        parsed: dict[str, Any] = response.json()
                    except ValueError as exc:
                        # A 200 with an unparseable body is the provider's
                        # fault, but retrying it is reasonable.
                        last = ProviderUnavailableError(
                            f"Tavily returned a response that is not JSON: {exc}"
                        )
                    else:
                        if not isinstance(parsed, dict):
                            last = ProviderUnavailableError(
                                "Tavily returned JSON that is not an object."
                            )
                        else:
                            return parsed
                else:
                    last = _normalise_status(
                        response.status_code,
                        detail=response.text,
                        retry_after=_parse_retry_after(response.headers),
                    )

            log.warning(
                "search_call_failed",
                provider=PROVIDER_NAME,
                code=last.code,
                retryable=last.retryable,
                attempt=attempt,
                query_chars=len(query),
            )
            if not last.retryable or attempt == self._max_attempts:
                break

            delay = last.retry_after_seconds or self._backoff(attempt)
            log.info(
                "search_call_retrying",
                provider=PROVIDER_NAME,
                delay_seconds=round(delay, 2),
                honoured_provider_hint=last.retry_after_seconds is not None,
            )
            await asyncio.sleep(delay)

        raise last or ProviderUnavailableError("Tavily could not be reached.")

    @staticmethod
    def _backoff(attempt: int) -> float:
        ceiling = min(_BACKOFF_BASE_SECONDS * 2 ** (attempt - 1), _BACKOFF_CAP_SECONDS)
        return random.uniform(0, ceiling)  # noqa: S311  (not cryptographic)

    def _to_results(self, body: dict[str, Any]) -> list[SearchResult]:
        """Convert a response body into results, dropping what cannot be used.

        Tolerant of a malformed payload by design: one unusable entry costs a
        source, whereas raising would discard an otherwise good result set.
        Every drop is logged so a provider that starts returning rubbish is
        visible rather than silently halving the evidence.
        """
        raw_results = body.get("results")
        if not isinstance(raw_results, list):
            log.warning("search_response_missing_results", provider=PROVIDER_NAME)
            return []

        results: list[SearchResult] = []
        dropped_unsafe = 0
        dropped_malformed = 0
        seen: set[str] = set()

        for item in raw_results:
            if not isinstance(item, dict):
                dropped_malformed += 1
                continue

            url = item.get("url")
            if not isinstance(url, str) or not url.strip():
                dropped_malformed += 1
                continue

            try:
                safe_url = validate_url(url)
            except UnsafeUrlError as exc:
                dropped_unsafe += 1
                log.warning(
                    "search_result_url_rejected",
                    provider=PROVIDER_NAME,
                    reason=exc.code,
                    detail=exc.message,
                )
                continue

            # Deduplicate within one response as well as across subtasks: the
            # same page often appears twice under different tracking URLs.
            if safe_url in seen:
                continue
            seen.add(safe_url)

            excerpt = item.get("content")
            title = item.get("title")
            score = item.get("score")
            published = item.get("published_date")

            results.append(
                SearchResult(
                    url=safe_url,
                    title=str(title).strip()
                    if isinstance(title, str) and title.strip()
                    else safe_url,
                    excerpt=(
                        str(excerpt)[:MAX_EXCERPT_CHARS]
                        if isinstance(excerpt, str) and excerpt.strip()
                        else ""
                    ),
                    rank=len(results),
                    provider=PROVIDER_NAME,
                    is_fixture=False,
                    score=float(score) if isinstance(score, int | float) else None,
                    published_date=(
                        str(published)[:64]
                        if isinstance(published, str) and published.strip()
                        else None
                    ),
                )
            )

        if dropped_unsafe or dropped_malformed:
            log.warning(
                "search_results_dropped",
                provider=PROVIDER_NAME,
                unsafe=dropped_unsafe,
                malformed=dropped_malformed,
                kept=len(results),
            )

        log.info(
            "search_completed",
            provider=PROVIDER_NAME,
            results=len(results),
            query_chars=len(str(body.get("query", ""))),
        )
        return results
