"""The fixture search provider.

The offline demo and CI both depend on this being completely deterministic:
the same query must return the same ranked sources on every machine, or the
"repeatable offline demo" claim is false.
"""

from __future__ import annotations

import pytest

from app.adapters.search.fixture import FixtureSearchProvider, load_corpus
from app.domain.enums import SearchMode

QUERY = "airflow prefect dagster operational burden small team self-host"


class TestCorpus:
    def test_the_committed_corpus_loads(self) -> None:
        documents = load_corpus()
        assert len(documents) >= 10

    def test_every_document_declares_a_fixture_url(self) -> None:
        """A fixture URL must be unmistakable, so a reader can never confuse
        a fixture source for something retrieved from the live web."""
        for document in load_corpus():
            assert "fixtures.agentflow.test" in document.url

    def test_the_corpus_contains_a_red_team_document(self) -> None:
        redteam = [doc for doc in load_corpus() if doc.redteam]
        assert len(redteam) == 1
        assert "IGNORE ALL PREVIOUS INSTRUCTIONS" in redteam[0].excerpt


class TestDeterminism:
    async def test_the_same_query_returns_the_same_results(self) -> None:
        provider = FixtureSearchProvider()
        first = await provider.search(QUERY, max_results=5)
        second = await provider.search(QUERY, max_results=5)
        assert [r.url for r in first] == [r.url for r in second]

    async def test_a_fresh_provider_returns_the_same_results(self) -> None:
        """Ordering must not depend on in-process state."""
        a = await FixtureSearchProvider().search(QUERY, max_results=5)
        b = await FixtureSearchProvider().search(QUERY, max_results=5)
        assert [r.url for r in a] == [r.url for r in b]

    async def test_ranking_is_total_so_ties_cannot_reorder(self) -> None:
        provider = FixtureSearchProvider()
        results = await provider.search("comparison", max_results=10)
        assert [r.rank for r in results] == list(range(len(results)))


class TestRanking:
    async def test_results_are_capped(self) -> None:
        results = await FixtureSearchProvider().search(QUERY, max_results=3)
        assert len(results) <= 3

    async def test_a_query_with_no_overlap_returns_nothing(self) -> None:
        """Returning unrelated documents would make the demo look like it had
        found evidence it had not."""
        results = await FixtureSearchProvider().search(
            "quantum chromodynamics lattice", max_results=5
        )
        assert results == []

    @pytest.mark.parametrize(
        ("query", "expected_fragment"),
        [
            ("dagster assets testing", "dagster"),
            ("prefect dynamic flows", "prefect"),
            ("licensing open core apache", "licensing"),
        ],
    )
    async def test_queries_retrieve_topically_relevant_documents(
        self, query: str, expected_fragment: str
    ) -> None:
        results = await FixtureSearchProvider().search(query, max_results=3)
        assert results
        assert any(expected_fragment in r.url for r in results)


class TestLabelling:
    async def test_every_result_is_marked_as_a_fixture(self) -> None:
        results = await FixtureSearchProvider().search(QUERY, max_results=5)
        assert results
        assert all(r.is_fixture for r in results)
        assert all(r.provider == "fixture" for r in results)

    def test_the_provider_reports_fixture_mode(self) -> None:
        assert FixtureSearchProvider().mode is SearchMode.FIXTURE

    async def test_url_digests_are_stable_and_unique(self) -> None:
        results = await FixtureSearchProvider().search(QUERY, max_results=5)
        digests = [r.url_sha256 for r in results]
        assert len(set(digests)) == len(digests)
        assert all(len(d) == 64 for d in digests)
