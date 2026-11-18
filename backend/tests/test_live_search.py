"""Live web search acceptance tests.

These consume **Tavily credits** (1 per search at `basic` depth) and are
skipped unless `SEARCH_PROVIDER=tavily` and a real key are set. A credit
budget guard reads the provider's own reported usage before each test and
refuses to start once the approved allowance is spent.

Split deliberately by cost. Retrieval behaviour — real results, metadata,
deduplication, empty result sets — needs **no model calls**, so it is tested
separately from the end-to-end workflow. That matters when the model quota is
the scarcer resource: proving deduplication works should not cost four model
requests.
"""

from __future__ import annotations

import os
import uuid
from typing import Any, cast

import pytest
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy import select

from app.adapters.search.registry import build_search_provider
from app.adapters.search.urls import normalise_url
from app.core.config import Settings
from app.domain.enums import SearchMode
from app.domain.errors import ApprovalRequiredError
from app.graph.deps import NodeDeps
from app.graph.state import GraphState
from tests.conftest import (
    LIVE_ENABLED,
    LIVE_SEARCH_API_KEY,
    LIVE_SEARCH_BASE_URL,
    LIVE_SEARCH_ENABLED,
    create_task_row,
)

pytestmark = [
    pytest.mark.live_search,
    pytest.mark.skipif(
        not LIVE_SEARCH_ENABLED,
        reason="live search not configured (set SEARCH_PROVIDER=tavily and SEARCH_API_KEY)",
    ),
]

QUERY = "apache airflow prefect dagster operational burden comparison"


def live_provider(**overrides: object) -> object:
    settings = Settings(
        _env_file=None,
        app_env="test",
        search_provider="tavily",
        search_api_key=LIVE_SEARCH_API_KEY,
        search_base_url=LIVE_SEARCH_BASE_URL,
        search_depth="basic",
        **overrides,  # type: ignore[arg-type]
    )
    return build_search_provider(settings)


class TestRealRetrieval:
    """One search each. No model calls."""

    async def test_a_real_query_returns_usable_web_sources(self, search_budget_guard: int) -> None:
        provider = live_provider()
        try:
            results = await provider.search(QUERY, max_results=5)  # type: ignore[attr-defined]
        finally:
            await provider.aclose()  # type: ignore[attr-defined]

        assert results, "a mainstream query should return sources"
        for found in results:
            # Live, and labelled as live. The whole provenance story depends
            # on this flag being right at the source level.
            assert found.is_fixture is False
            assert found.provider == "tavily"
            assert found.url.startswith(("http://", "https://"))
            # Not a fixture host: these must be real pages.
            assert "fixtures.agentflow.test" not in found.url
            assert found.title
            assert found.excerpt, "an excerpt is what grounds a claim"

    async def test_retrieved_urls_are_already_normalised(self, search_budget_guard: int) -> None:
        """Validation returns the normalised form, so a caller cannot store
        the raw URL by accident."""
        provider = live_provider()
        try:
            results = await provider.search(QUERY, max_results=5)  # type: ignore[attr-defined]
        finally:
            await provider.aclose()  # type: ignore[attr-defined]

        for found in results:
            assert found.url == normalise_url(found.url)
            assert "#" not in found.url
            assert "utm_source" not in found.url

    async def test_results_are_unique_and_contiguously_ranked(
        self, search_budget_guard: int
    ) -> None:
        provider = live_provider()
        try:
            results = await provider.search(QUERY, max_results=8)  # type: ignore[attr-defined]
        finally:
            await provider.aclose()  # type: ignore[attr-defined]

        urls = [r.url for r in results]
        digests = [r.url_sha256 for r in results]
        assert len(set(urls)) == len(urls), "the same page must not appear twice"
        assert len(set(digests)) == len(digests)
        assert [r.rank for r in results] == list(range(len(results)))

    async def test_provider_metadata_is_preserved(self, search_budget_guard: int) -> None:
        """Score and publication date are real provider output, not invented."""
        provider = live_provider()
        try:
            results = await provider.search(QUERY, max_results=5)  # type: ignore[attr-defined]
        finally:
            await provider.aclose()  # type: ignore[attr-defined]

        assert results
        scored = [r for r in results if r.score is not None]
        assert scored, "Tavily reports a relevance score"
        assert all(0.0 <= r.score <= 1.0 for r in scored)  # type: ignore[operator]
        # Dates are optional per result; the field must round-trip when present.
        for found in results:
            if found.published_date is not None:
                assert found.published_date.strip()

    async def test_the_provider_reports_live_mode(self) -> None:
        """No search performed: a configuration assertion only."""
        provider = live_provider()
        try:
            assert provider.mode is SearchMode.TAVILY  # type: ignore[attr-defined]
        finally:
            await provider.aclose()  # type: ignore[attr-defined]


class TestEmptyAndUnusualQueries:
    async def test_a_nonsense_query_returns_few_or_no_results_without_failing(
        self, search_budget_guard: int
    ) -> None:
        """An empty result set is a finding about the query. It must not be
        confused with a retrieval failure, and must not raise."""
        provider = live_provider()
        try:
            results = await provider.search(
                "zzqxwv nonexistent gibberish phrase qqzz 8f3a1b", max_results=5
            )  # type: ignore[attr-defined]
        finally:
            await provider.aclose()  # type: ignore[attr-defined]

        assert isinstance(results, list)
        for found in results:
            assert found.url.startswith(("http://", "https://"))


class TestCreditAccounting:
    """What our own code spends, not what the provider reports when.

    An earlier version of this test asserted that `plan_usage` rises by one
    immediately after a search. It does not: Tavily's usage reporting lags,
    and the endpoint has its own 10-requests-per-10-minutes limit, so polling
    it to measure a single search is both unreliable and wasteful. The
    assertion that matters is how many searches **we** issue.
    """

    async def test_one_search_per_query_at_the_configured_depth(
        self, search_budget_guard: int
    ) -> None:
        import json

        import httpx

        requests: list[dict[str, object]] = []
        provider = live_provider()
        inner = provider._client  # type: ignore[attr-defined]

        def record(request: httpx.Request) -> httpx.Response:
            requests.append(json.loads(request.content))
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "url": "https://example.com/a",
                            "title": "t",
                            "content": "c",
                            "score": 0.5,
                        }
                    ]
                },
            )

        # The transport is swapped so this test costs **zero** credits while
        # still exercising the configured provider: what is being verified is
        # our request shape, which determines the credit cost.
        provider._client = httpx.AsyncClient(transport=httpx.MockTransport(record))  # type: ignore[attr-defined]
        try:
            await provider.search("one query", max_results=3)  # type: ignore[attr-defined]
        finally:
            await provider.aclose()  # type: ignore[attr-defined]
            await inner.aclose()

        assert len(requests) == 1, "one query must cost one search"
        assert requests[0]["search_depth"] == "basic", (
            "basic depth costs 1 Tavily credit; advanced costs 2, and the "
            "credit budget is built on basic"
        )
        assert requests[0]["include_raw_content"] is False

    async def test_the_per_task_cap_is_configured_as_approved(self) -> None:
        """No call made: guards the approved ceiling against drift."""
        from app.core.config import Settings

        settings = Settings(
            _env_file=None,
            app_env="test",
            search_provider="tavily",
            search_api_key=LIVE_SEARCH_API_KEY,
        )
        assert settings.search_max_per_task == 8
        assert settings.search_depth == "basic"


# ---------------------------------------------------------------------------
# The complete lifecycle on live search + live models
# ---------------------------------------------------------------------------


# ``live_models`` separates this from the search-only sweep. The module-level
# ``pytestmark`` applies ``live_search`` to every class here, so ``-m
# live_search`` alone selects this test too -- which is how a search-only
# batch, run when four model requests remained, consumed seven of them and
# aborted mid-run. Search-only batches must select
# ``-m "live_search and not live_models"``.
@pytest.mark.live
@pytest.mark.live_models
@pytest.mark.integration
@pytest.mark.skipif(
    not LIVE_ENABLED, reason="live models not configured (set LLM_PROVIDER=openrouter)"
)
@pytest.mark.timeout(2400)
class TestLiveResearchLifecycle:
    """One task, asserted from every angle the milestone requires.

    Deliberately a single test rather than several: a full run costs four
    model calls plus three searches, and re-deriving the same facts from a
    second run would spend scarce quota to learn nothing. The lifecycle —
    run, approve, export, revise — is asserted in sequence on one task.
    """

    async def test_the_full_lifecycle_on_live_search_and_live_models(
        self, live_search_deps: NodeDeps, live_database: str
    ) -> None:
        from redis.asyncio import Redis

        from app.adapters.queue.base import TaskQueue
        from app.db.models import ClaimSource, LLMCall, Report, ReportClaim, ResearchTask, Source
        from app.domain.enums import ApprovalAction, ReportStatus, SupportStatus, TaskStatus
        from app.services.task_service import TaskService
        from app.workers.jobs import resume_task, run_task

        deps = live_search_deps
        dsn = live_database.replace("postgresql+asyncpg://", "postgresql://", 1)
        redis = Redis.from_url(
            os.environ.get("REDIS_URL", "redis://localhost:6379/0"), decode_responses=True
        )

        class RecordingQueue:
            """Captures what the service would enqueue; the worker is driven
            directly so the test does not depend on a running arq process."""

            def __init__(self) -> None:
                self.runs: list[str] = []
                self.resumes: list[tuple[str, dict[str, Any]]] = []

            async def enqueue_run(self, task_id: str) -> None:
                self.runs.append(task_id)

            async def enqueue_resume(self, task_id: str, decision: dict[str, Any]) -> None:
                self.resumes.append((task_id, decision))

            async def ping(self) -> bool:
                return True

        queue = RecordingQueue()
        session_id = "live-m4"

        # ---- create and run -------------------------------------------
        async with deps.session_factory() as session:
            service = TaskService(
                session=session, settings=deps.settings, queue=cast(TaskQueue, queue)
            )
            task = await service.create(
                session_id=session_id,
                question=(
                    "Compare Apache Airflow, Prefect and Dagster for a small "
                    "engineering team, focusing on operational burden."
                ),
            )
            await session.commit()
            task_id = str(task.id)

        assert queue.runs == [task_id]

        async with AsyncPostgresSaver.from_conn_string(dsn) as checkpointer:
            await checkpointer.setup()
            ctx: dict[str, Any] = {
                "redis": redis,
                "session_factory": deps.session_factory,
                "node_deps": deps,
                # The worker always builds both; a task admitted to the
                # offline profile must run there even on a live deployment.
                "offline_deps": deps,
                "checkpointer": checkpointer,
                "settings": deps.settings,
            }
            try:
                outcome = await run_task(ctx, task_id)
                assert outcome == TaskStatus.AWAITING_APPROVAL.value

                # ---- real web retrieval ------------------------------
                async with deps.session_factory() as session:
                    sources = list(
                        (
                            await session.scalars(
                                select(Source).where(Source.task_id == uuid.UUID(task_id))
                            )
                        ).all()
                    )
                assert sources, "live search should have produced sources"
                assert all(not s.is_fixture for s in sources), "these must be live"
                assert all(s.provider == "tavily" for s in sources)
                assert not any("fixtures.agentflow.test" in s.url for s in sources)

                # ---- deduplication, on real results ------------------
                digests = [s.url_sha256 for s in sources]
                assert len(set(digests)) == len(digests)
                assert all(s.url == normalise_url(s.url) for s in sources)

                # ---- citation support over real sources --------------
                async with deps.session_factory() as session:
                    report = await session.scalar(
                        select(Report).where(Report.task_id == uuid.UUID(task_id))
                    )
                    assert report is not None
                    claims = list(
                        (
                            await session.scalars(
                                select(ReportClaim).where(ReportClaim.report_id == report.id)
                            )
                        ).all()
                    )
                    links = (
                        await session.execute(
                            select(ClaimSource.claim_id, ClaimSource.source_id).where(
                                ClaimSource.claim_id.in_([c.id for c in claims] or [None])
                            )
                        )
                    ).all()
                    calls = list(
                        (
                            await session.scalars(
                                select(LLMCall).where(LLMCall.task_id == uuid.UUID(task_id))
                            )
                        ).all()
                    )

                assert claims, "the workflow should produce checked claims"
                retrieved = {s.id for s in sources}
                for _, source_id in links:
                    assert source_id in retrieved, "a citation must resolve to a real source"
                for claim in claims:
                    if claim.support_status is not SupportStatus.UNSUPPORTED:
                        assert claim.claim_text

                # ---- provenance: live model, live sources -------------
                markdown = report.content_markdown
                assert "retrieved, not verified" in markdown
                assert "does not assess" in markdown
                assert "fixture corpus" not in markdown
                assert "No language model was called" not in markdown
                assert "Written by model(s)" in markdown

                # ---- usage accounting --------------------------------
                assert calls
                assert all(c.provider_served for c in calls)
                assert all(c.tokens_in > 0 and c.tokens_out > 0 for c in calls)

                # ---- export is refused before approval ---------------
                async with deps.session_factory() as session:
                    service = TaskService(
                        session=session, settings=deps.settings, queue=cast(TaskQueue, queue)
                    )
                    with pytest.raises(ApprovalRequiredError):
                        await service.export_markdown(
                            task_id=uuid.UUID(task_id), session_id=session_id
                        )

                # ---- revision ----------------------------------------
                async with deps.session_factory() as session:
                    service = TaskService(
                        session=session, settings=deps.settings, queue=cast(TaskQueue, queue)
                    )
                    _, version = await service.request_revision(
                        task_id=uuid.UUID(task_id),
                        session_id=session_id,
                        feedback="Say more about the licensing boundary.",
                    )
                    await session.commit()
                assert version == 1
                assert queue.resumes[-1][1]["action"] == ApprovalAction.REQUEST_REVISION.value

                await resume_task(ctx, task_id, queue.resumes[-1][1])

                async with deps.session_factory() as session:
                    reports = list(
                        (
                            await session.scalars(
                                select(Report)
                                .where(Report.task_id == uuid.UUID(task_id))
                                .order_by(Report.version)
                            )
                        ).all()
                    )
                    refreshed = await session.get(ResearchTask, uuid.UUID(task_id))
                assert [r.version for r in reports] == [1, 2]
                assert reports[0].status is ReportStatus.SUPERSEDED
                assert refreshed is not None
                assert refreshed.status is TaskStatus.AWAITING_APPROVAL

                # ---- approval unlocks export -------------------------
                async with deps.session_factory() as session:
                    service = TaskService(
                        session=session, settings=deps.settings, queue=cast(TaskQueue, queue)
                    )
                    approved, approved_version = await service.approve(
                        task_id=uuid.UUID(task_id), session_id=session_id
                    )
                    await session.commit()
                assert approved.status is TaskStatus.APPROVED
                assert approved_version == 2

                async with deps.session_factory() as session:
                    service = TaskService(
                        session=session, settings=deps.settings, queue=cast(TaskQueue, queue)
                    )
                    exported, exported_version = await service.export_markdown(
                        task_id=uuid.UUID(task_id), session_id=session_id
                    )
                assert exported_version == 2
                assert "## Limitations" in exported
                assert "retrieved, not verified" in exported
                assert len(exported) > 500
            finally:
                await redis.aclose()


@pytest.mark.live_search
@pytest.mark.integration
@pytest.mark.skipif(
    not LIVE_SEARCH_API_KEY, reason="live search not configured (set SEARCH_API_KEY)"
)
class TestLivePartialFailure:
    """Partial retrieval failure, provoked by the real provider.

    The offline suite already covers this path with a stubbed error. What it
    cannot show is that a *genuine* Tavily rejection is classified as a
    per-subtask failure rather than a task-killing one, so this drives the
    node against the live API with one query the provider will refuse.
    """

    async def test_one_subtask_failing_live_leaves_the_rest_usable(
        self, live_search_only_deps: NodeDeps
    ) -> None:
        from app.db.models import Source, WorkflowStep
        from app.graph.nodes.research import research_node

        deps = live_search_only_deps
        task_id = await create_task_row(
            deps, question="Partial retrieval failure against the live provider."
        )

        # An over-long query is rejected with
        # ``400 {"detail":{"error":"Query is too long. ..."}}``. The length
        # here was measured against the live API rather than assumed: the
        # error names a 1500-character limit, but a 1649-character query was
        # accepted in practice, so the test uses a length well past any
        # observed tolerance. A 400 is non-retryable, so this costs one
        # request and no search credit.
        too_long = "operational burden of workflow orchestration platforms " * 75
        assert len(too_long) > 4000
        state: GraphState = {
            "task_id": task_id,
            "question": "Partial retrieval failure against the live provider.",
            "search_queries": [
                "Apache Airflow scheduler operational burden",
                too_long,
                "Dagster asset based orchestration tradeoffs",
            ],
        }

        result = await research_node(state, deps)

        # The failure is counted, not swallowed...
        assert result["search_failures"] == 1
        # ...and the surviving subtasks still produced real evidence.
        assert result["source_ids"]

        async with deps.session_factory() as session:
            sources = list(
                (
                    await session.scalars(
                        select(Source).where(Source.task_id == uuid.UUID(task_id))
                    )
                ).all()
            )
            step = await session.scalar(
                select(WorkflowStep).where(WorkflowStep.task_id == uuid.UUID(task_id))
            )

        assert len(sources) == len(result["source_ids"])
        assert all(not s.is_fixture for s in sources)
        assert step is not None
        assert step.output_json is not None
        assert step.output_json["partial"] is True
        assert step.output_json["subtasks_failed"] == 1
        assert step.output_json["failures"][0]["subtask"] == "1"
        # The timeline records which subtask failed and why, so a reader does
        # not have to guess why the evidence is thinner than the plan.
        assert step.output_json["failures"][0]["code"]
        assert step.output_json["is_fixture"] is False
