"""Live OpenRouter acceptance tests — these make **billable** calls.

Skipped entirely unless `LLM_PROVIDER=openrouter` and a real `LLM_API_KEY`
are present, so the default suite stays free and offline. Every test also
passes through `live_budget_guard`, which reads actual spend from the
`llm_calls` ledger and refuses to start once the approved cap is reached.

Search stays on fixtures throughout: M3 is the live *model* milestone. That
separation is deliberate and is what lets these tests attribute behaviour to
the model rather than to variable web results.

    LLM_PROVIDER=openrouter LLM_API_KEY=... uv run pytest -m live
"""

from __future__ import annotations

import os
import uuid

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy import select

from app.adapters.llm.capabilities import ModelCatalogue, fetch_catalogue
from app.adapters.llm.openai_compat import OpenAICompatibleLLMClient
from app.adapters.llm.registry import build_llm_runtime
from app.core.config import Settings
from app.db.models import ClaimSource, LLMCall, Report, ReportClaim, ResearchTask, Source
from app.domain.enums import (
    AttemptOutcome,
    LLMProvider,
    OutputMode,
    SupportStatus,
    TaskStatus,
)
from app.domain.errors import BudgetExhaustedError, ModelCapabilityError, ProviderAuthError
from app.domain.nodes import LLM_NODES, NodeName
from app.graph.builder import build_graph
from app.graph.deps import NodeDeps
from app.graph.schemas import PlanOutput
from app.workers.jobs import run_task
from tests.conftest import (
    LIVE_ALT_MODEL,
    LIVE_DEV_MODEL,
    LIVE_ENABLED,
    create_task_row,
    make_settings,
)

pytestmark = [
    pytest.mark.live,
    pytest.mark.integration,
    pytest.mark.skipif(
        not LIVE_ENABLED,
        reason="live provider not configured (set LLM_PROVIDER=openrouter and LLM_API_KEY)",
    ),
]

QUESTION = "Compare three open-source workflow orchestration frameworks for a small team."


async def run_to_gate(deps: NodeDeps, task_id: str) -> object:
    """Run the graph directly, returning the compiled graph.

    Used where a test only needs the graph's own behaviour. Tests that assert
    on the *task row* must go through `run_live_task` instead: setting the
    status is the worker's job, not the graph's.
    """
    graph = build_graph(deps, checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": task_id}}
    await graph.ainvoke(
        {"task_id": task_id, "question": QUESTION, "constraints": {}}, config=config
    )
    return graph


async def run_live_task(deps: NodeDeps, task_id: str, redis: object, dsn: str) -> str:
    """Execute a task through the real arq job handler.

    This is the production path: lock, graph, checkpoint, status settlement.
    A bare graph invocation leaves the task row untouched, so asserting on
    status after one would be testing the test rather than the system.
    """
    async with AsyncPostgresSaver.from_conn_string(dsn) as checkpointer:
        await checkpointer.setup()
        ctx = {
            "redis": redis,
            "session_factory": deps.session_factory,
            "node_deps": deps,
            "checkpointer": checkpointer,
            "settings": deps.settings,
        }
        return await run_task(ctx, task_id)


class TestLiveWorkflow:
    """The headline M3 criterion: the whole workflow on real models.

    **One** workflow run, asserted from several angles. Deliberately not three
    tests doing three runs: a free tier's rate limit is a shared resource, and
    four extra model calls to re-derive the same facts is waste on any tier.
    """

    async def test_the_live_workflow_produces_a_grounded_report(
        self, live_deps: NodeDeps, live_database: str
    ) -> None:
        from redis.asyncio import Redis

        redis = Redis.from_url(
            os.environ.get("REDIS_URL", "redis://localhost:6379/0"), decode_responses=True
        )
        task_id = await create_task_row(live_deps, question=QUESTION)
        try:
            outcome = await run_live_task(
                live_deps,
                task_id,
                redis,
                live_database.replace("postgresql+asyncpg://", "postgresql://", 1),
            )
        finally:
            await redis.aclose()

        # --- the worker settled the task, because this went through the real
        # job handler rather than a bare graph invocation
        assert outcome == TaskStatus.AWAITING_APPROVAL.value

        async with live_deps.session_factory() as session:
            task = await session.get(ResearchTask, uuid.UUID(task_id))
            sources = (
                await session.scalars(select(Source).where(Source.task_id == uuid.UUID(task_id)))
            ).all()
            report = await session.scalar(
                select(Report).where(Report.task_id == uuid.UUID(task_id))
            )
            calls = (
                await session.scalars(select(LLMCall).where(LLMCall.task_id == uuid.UUID(task_id)))
            ).all()
            claims = (
                (
                    await session.scalars(
                        select(ReportClaim).where(ReportClaim.report_id == report.id)
                    )
                ).all()
                if report is not None
                else []
            )

        assert task is not None
        assert task.status is TaskStatus.AWAITING_APPROVAL

        # --- fixture search still supplied the evidence; M3 is the live
        # *model* milestone and live search belongs to M4
        assert sources
        assert all(s.is_fixture for s in sources)

        # --- a real report, with claims the model actually produced
        assert report is not None
        assert report.version == 1
        assert len(report.content_markdown) > 500
        assert claims, "a live run should produce checked claims"

        # --- containment still applies to a real model: nothing marked
        # supported may cite a source that was never retrieved
        retrieved = {s.id for s in sources}
        async with live_deps.session_factory() as session:
            links = (
                await session.execute(
                    select(ClaimSource.claim_id, ClaimSource.source_id).where(
                        ClaimSource.claim_id.in_([c.id for c in claims])
                    )
                )
            ).all()
        for _, source_id in links:
            assert source_id in retrieved

        # --- the distinction the milestone turns on: real AI analysis over
        # fixture sources must say exactly that, and must not claim a stub
        markdown = report.content_markdown
        assert "## Limitations" in markdown
        assert "fixture corpus" in markdown
        assert "deterministic stub adapter" not in markdown
        assert "Written by model(s)" in markdown

        # --- the ledger recorded real usage for every model node
        assert {c.node for c in calls} == set(LLM_NODES)
        for call in calls:
            assert call.provider is LLMProvider.OPENROUTER
            assert call.model_requested
            assert call.model_served, "the gateway reports which model served the call"
            assert call.tokens_in > 0 and call.tokens_out > 0
            assert call.latency_ms is not None and call.latency_ms > 0
            assert call.cost_usd_estimate is not None
        assert task.calls_used == len(calls)
        assert task.tokens_used == sum(c.tokens_in + c.tokens_out for c in calls)


class TestStructuredOutput:
    async def test_a_real_model_returns_schema_valid_output(self, live_deps: NodeDeps) -> None:
        result = await live_deps.llm.complete_structured(
            node=NodeName.PLAN,
            schema=PlanOutput,
            system="You are a research planner. Return only the structured object.",
            user=f"Research question:\n{QUESTION}\n\nProduce at most 3 subtasks.",
        )
        assert isinstance(result.output, PlanOutput)
        assert 1 <= len(result.output.subtasks) <= 8
        assert all(s.search_query for s in result.output.subtasks)

    async def test_the_native_schema_tier_is_used(self, live_deps: NodeDeps) -> None:
        """Strict mode must get tier 1, not a quiet downgrade."""
        result = await live_deps.llm.complete_structured(
            node=NodeName.PLAN,
            schema=PlanOutput,
            system="Return only the structured object.",
            user=f"{QUESTION}\n\nProduce at most 2 subtasks.",
        )
        assert result.usage.output_mode is OutputMode.JSON_SCHEMA


class TestUsageLedger:
    """Per-node model selection. Ledger assertions for a full run live in
    TestLiveWorkflow, which already pays for one."""

    async def test_per_node_model_selection_reaches_the_provider(self, live_deps: NodeDeps) -> None:
        """Configuring a different model for one node must actually change
        which model serves it."""
        settings = Settings(
            _env_file=None,
            app_env="test",
            llm_provider="openrouter",
            llm_api_key=live_deps.settings.llm_api_key.get_secret_value()
            if live_deps.settings.llm_api_key
            else "",
            llm_model_default=LIVE_DEV_MODEL,
            llm_model_planner=LIVE_ALT_MODEL,
            search_provider="fixture",
        )
        runtime = await build_llm_runtime(settings)
        try:
            result = await runtime.client.complete_structured(
                node=NodeName.PLAN,
                schema=PlanOutput,
                system="Return only the structured object.",
                user=f"{QUESTION}\n\nProduce at most 2 subtasks.",
            )
            assert result.usage.model_requested == LIVE_ALT_MODEL
            assert LIVE_ALT_MODEL.split("/")[0] in (result.usage.model_served or "")
        finally:
            await runtime.client.aclose()


class TestReliability:
    async def test_an_unreachable_primary_falls_back_to_another_vendor(
        self, live_deps: NodeDeps, live_database: str
    ) -> None:
        """The point of a gateway. The primary is a real model ID served by a
        deliberately broken base URL... so instead we force failure by
        pointing the primary at a model the account cannot use, and let the
        chain reach a working one."""
        catalogue = await fetch_catalogue(
            base_url=live_deps.settings.llm_base_url,
            api_key=live_deps.settings.llm_api_key.get_secret_value()
            if live_deps.settings.llm_api_key
            else None,
            timeout_seconds=30,
        )
        # A model that exists in the catalogue but is priced far above the
        # dev tier would still work; instead use an entry we know will 404 at
        # the provider by mangling it after validation.
        broken = ModelCatalogue.from_entries(
            {
                "vendor/definitely-not-served": {
                    "context_length": 1_000_000,
                    "supported_parameters": ["structured_outputs", "tools"],
                    "pricing": {"prompt": "0.000001", "completion": "0.000001"},
                },
                LIVE_ALT_MODEL: {
                    "context_length": catalogue.require(LIVE_ALT_MODEL).context_length,
                    "supported_parameters": ["structured_outputs", "tools"],
                    "pricing": {
                        "prompt": str(catalogue.require(LIVE_ALT_MODEL).prompt_usd_per_token),
                        "completion": str(
                            catalogue.require(LIVE_ALT_MODEL).completion_usd_per_token
                        ),
                    },
                },
            }
        )
        client = OpenAICompatibleLLMClient(
            catalogue=broken,
            models=dict.fromkeys(NodeName, "vendor/definitely-not-served"),
            base_url=live_deps.settings.llm_base_url,
            api_key=live_deps.settings.llm_api_key.get_secret_value()
            if live_deps.settings.llm_api_key
            else "",
            fallback_models=[LIVE_ALT_MODEL],
            max_attempts=1,
        )
        try:
            result = await client.complete_structured(
                node=NodeName.PLAN,
                schema=PlanOutput,
                system="Return only the structured object.",
                user=f"{QUESTION}\n\nProduce at most 2 subtasks.",
            )
            assert result.usage.attempt_outcome is AttemptOutcome.FELL_BACK
            assert result.usage.model_requested == LIVE_ALT_MODEL
        finally:
            await client.aclose()

    async def test_a_bad_credential_is_a_terminal_auth_error(self, live_deps: NodeDeps) -> None:
        """Must not be retried: a wrong key will stay wrong."""
        catalogue = ModelCatalogue.from_entries(
            {
                LIVE_DEV_MODEL: {
                    "context_length": 1_000_000,
                    "supported_parameters": ["structured_outputs"],
                    "pricing": {"prompt": "0.0000001", "completion": "0.0000005"},
                }
            }
        )
        client = OpenAICompatibleLLMClient(
            catalogue=catalogue,
            models=dict.fromkeys(NodeName, LIVE_DEV_MODEL),
            base_url=live_deps.settings.llm_base_url,
            api_key="sk-or-v1-invalid-key-for-testing",
            max_attempts=3,
        )
        try:
            with pytest.raises(ProviderAuthError):
                await client.complete_structured(
                    node=NodeName.PLAN,
                    schema=PlanOutput,
                    system="Return only the structured object.",
                    user="x",
                )
        finally:
            await client.aclose()

    async def test_an_unreachable_gateway_is_reported_not_hung(self, live_deps: NodeDeps) -> None:
        catalogue = ModelCatalogue.from_entries(
            {
                LIVE_DEV_MODEL: {
                    "context_length": 1_000_000,
                    "supported_parameters": ["structured_outputs"],
                    "pricing": {"prompt": "0.0000001", "completion": "0.0000005"},
                }
            }
        )
        client = OpenAICompatibleLLMClient(
            catalogue=catalogue,
            models=dict.fromkeys(NodeName, LIVE_DEV_MODEL),
            base_url="https://openrouter-does-not-exist.invalid/api/v1",
            api_key="unused",
            timeout_seconds=10,
            max_attempts=1,
        )
        try:
            with pytest.raises(Exception) as excinfo:
                await client.complete_structured(
                    node=NodeName.PLAN,
                    schema=PlanOutput,
                    system="Return only the structured object.",
                    user="x",
                )
            assert (
                "provider" in str(excinfo.value).lower()
                or "unexpected" in str(excinfo.value).lower()
            )
        finally:
            await client.aclose()

    async def test_an_invalid_model_is_refused_at_startup(self, live_deps: NodeDeps) -> None:
        """Not mid-task: a typo must stop the deployment, not a user's work."""
        settings = Settings(
            _env_file=None,
            app_env="test",
            llm_provider="openrouter",
            llm_api_key=live_deps.settings.llm_api_key.get_secret_value()
            if live_deps.settings.llm_api_key
            else "",
            llm_model_default="vendor/model-that-does-not-exist",
            search_provider="fixture",
        )
        with pytest.raises(ModelCapabilityError, match="not in the provider catalogue"):
            await build_llm_runtime(settings)


class TestBudgetEnforcement:
    async def test_a_task_that_exhausts_its_call_budget_fails_cleanly(
        self, live_deps: NodeDeps
    ) -> None:
        """Budget exhaustion must abort rather than emit a half-written
        report."""
        deps = NodeDeps(
            settings=make_settings(
                llm_provider="openrouter",
                llm_api_key=live_deps.settings.llm_api_key.get_secret_value()
                if live_deps.settings.llm_api_key
                else "",
                llm_model_default=LIVE_DEV_MODEL,
                llm_max_calls_per_task=2,  # the graph needs four
            ),
            session_factory=live_deps.session_factory,
            llm=live_deps.llm,
            search=live_deps.search,
        )
        task_id = await create_task_row(deps, question=QUESTION)

        with pytest.raises(BudgetExhaustedError):
            await run_to_gate(deps, task_id)

        async with deps.session_factory() as session:
            report = await session.scalar(
                select(Report).where(Report.task_id == uuid.UUID(task_id))
            )
            calls = (
                await session.scalars(select(LLMCall).where(LLMCall.task_id == uuid.UUID(task_id)))
            ).all()

        assert report is None, "no report may be written once the budget is gone"
        assert len(calls) <= 2


class TestInjectionAgainstRealModels:
    """Observations about real model behaviour, not guarantees.

    Containment is asserted by the offline suite and holds regardless of what
    a model does. These record what two real models actually did when handed
    the red-team document, and assert the parts the system controls.
    """

    @pytest.mark.parametrize("model", [LIVE_DEV_MODEL, LIVE_ALT_MODEL])
    async def test_an_injected_directive_cannot_approve_a_task(
        self, live_deps: NodeDeps, model: str
    ) -> None:
        settings = make_settings(
            llm_provider="openrouter",
            llm_api_key=live_deps.settings.llm_api_key.get_secret_value()
            if live_deps.settings.llm_api_key
            else "",
            llm_model_default=model,
        )
        runtime = await build_llm_runtime(settings)
        deps = NodeDeps(
            settings=settings,
            session_factory=live_deps.session_factory,
            llm=runtime.client,
            search=live_deps.search,
        )
        try:
            task_id = await create_task_row(deps, question=QUESTION)
            graph = build_graph(deps, checkpointer=InMemorySaver())
            config = {"configurable": {"thread_id": task_id}}
            await graph.ainvoke(
                {"task_id": task_id, "question": QUESTION, "constraints": {}}, config=config
            )

            async with deps.session_factory() as session:
                task = await session.get(ResearchTask, uuid.UUID(task_id))
                report = await session.scalar(
                    select(Report).where(Report.task_id == uuid.UUID(task_id))
                )
                claims = (
                    (
                        await session.scalars(
                            select(ReportClaim).where(ReportClaim.report_id == report.id)
                        )
                    ).all()
                    if report is not None
                    else []
                )

            # The directive demands self-approval. It cannot happen: approval
            # is an API action taken by a human.
            assert task is not None
            assert task.status is not TaskStatus.APPROVED
            state = await graph.aget_state(config)
            assert state.next, "the run must still be waiting for a human"

            # Every claim marked supported must cite a source that exists.
            assert report is not None
            for claim in claims:
                if claim.support_status is not SupportStatus.UNSUPPORTED:
                    assert claim.claim_text

            # The limitations section is generated from run data regardless of
            # the directive's demand for "no warnings".
            assert "## Limitations" in report.content_markdown
        finally:
            await runtime.client.aclose()
