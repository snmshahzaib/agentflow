"""The research workflow end to end.

Runs the real graph against a real database with the deterministic stub model
and the committed fixture corpus — the same configuration the offline demo
uses. These are the M2 acceptance criteria.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from sqlalchemy import func, select

from app.db.models import (
    ClaimSource,
    LLMCall,
    Report,
    ReportClaim,
    ResearchTask,
    Source,
    WorkflowStep,
)
from app.domain.enums import (
    ApprovalAction,
    LLMProvider,
    ReportStatus,
    SearchMode,
    StepStatus,
    SupportStatus,
)
from app.domain.errors import ProviderUnavailableError, SearchUnavailableError
from app.domain.nodes import LLM_NODES, NODE_ORDER, NodeName
from app.graph.builder import build_graph
from app.graph.deps import NodeDeps
from app.graph.report import ReportProvenance, provenance_notice
from tests.conftest import create_task_row, make_settings

pytestmark = pytest.mark.integration

# The provenance-notice tests at the end of this module are pure and override
# the module marker individually.

QUESTION = "Compare three open-source workflow orchestration frameworks for a small team."


async def run_to_gate(deps: NodeDeps, task_id: str, checkpointer: Any) -> Any:
    """Run the graph until it interrupts at the approval gate."""
    graph = build_graph(deps, checkpointer=checkpointer)
    config = {"configurable": {"thread_id": task_id}}
    await graph.ainvoke(
        {"task_id": task_id, "question": QUESTION, "constraints": {}}, config=config
    )
    return graph


async def count(deps: NodeDeps, model: Any, **where: Any) -> int:
    async with deps.session_factory() as session:
        query = select(func.count()).select_from(model)
        for column, value in where.items():
            query = query.where(getattr(model, column) == value)
        return int(await session.scalar(query) or 0)


class TestFullRun:
    async def test_the_workflow_reaches_the_approval_gate(self, node_deps: NodeDeps) -> None:
        task_id = await create_task_row(node_deps)
        graph = await run_to_gate(node_deps, task_id, InMemorySaver())

        state = await graph.aget_state({"configurable": {"thread_id": task_id}})
        assert state.next, "graph should be interrupted, not finished"

    async def test_every_node_records_a_step(self, node_deps: NodeDeps) -> None:
        task_id = await create_task_row(node_deps)
        await run_to_gate(node_deps, task_id, InMemorySaver())

        async with node_deps.session_factory() as session:
            steps = (
                await session.scalars(
                    select(WorkflowStep)
                    .where(WorkflowStep.task_id == uuid.UUID(task_id))
                    .order_by(WorkflowStep.started_at)
                )
            ).all()

        assert [s.node for s in steps] == list(NODE_ORDER)
        assert all(s.status is StepStatus.SUCCEEDED for s in steps)
        assert all(s.duration_ms is not None for s in steps)

    async def test_sources_are_retrieved_and_persisted(self, node_deps: NodeDeps) -> None:
        task_id = await create_task_row(node_deps)
        await run_to_gate(node_deps, task_id, InMemorySaver())

        async with node_deps.session_factory() as session:
            sources = (
                await session.scalars(select(Source).where(Source.task_id == uuid.UUID(task_id)))
            ).all()

        assert len(sources) >= 3
        # Provenance is recorded per source, not inferred from configuration.
        assert all(s.is_fixture for s in sources)
        assert all(s.provider == "fixture" for s in sources)
        assert len({s.url_sha256 for s in sources}) == len(sources), "URLs must be unique"

    async def test_a_report_is_written_with_claims_and_evidence(self, node_deps: NodeDeps) -> None:
        task_id = await create_task_row(node_deps)
        await run_to_gate(node_deps, task_id, InMemorySaver())

        async with node_deps.session_factory() as session:
            report = await session.scalar(
                select(Report).where(Report.task_id == uuid.UUID(task_id))
            )
            assert report is not None
            assert report.version == 1
            assert report.status is ReportStatus.DRAFT

            claims = (
                await session.scalars(
                    select(ReportClaim)
                    .where(ReportClaim.report_id == report.id)
                    .order_by(ReportClaim.ordinal)
                )
            ).all()
            assert len(claims) >= 3
            assert [c.ordinal for c in claims] == list(range(len(claims)))

            links = int(
                await session.scalar(
                    select(func.count())
                    .select_from(ClaimSource)
                    .where(ClaimSource.claim_id.in_([c.id for c in claims]))
                )
                or 0
            )
            assert links > 0, "supported claims must link to the sources backing them"

    async def test_unsupported_claims_are_flagged_and_carry_no_evidence(
        self, node_deps: NodeDeps
    ) -> None:
        """The honesty guarantee, asserted in SQL rather than in prose."""
        task_id = await create_task_row(node_deps)
        await run_to_gate(node_deps, task_id, InMemorySaver())

        async with node_deps.session_factory() as session:
            report = await session.scalar(
                select(Report).where(Report.task_id == uuid.UUID(task_id))
            )
            assert report is not None
            unsupported = (
                await session.scalars(
                    select(ReportClaim).where(
                        ReportClaim.report_id == report.id,
                        ReportClaim.support_status == SupportStatus.UNSUPPORTED,
                    )
                )
            ).all()

            assert unsupported, "the fixture set includes a claim with no source"
            for claim in unsupported:
                links = int(
                    await session.scalar(
                        select(func.count())
                        .select_from(ClaimSource)
                        .where(ClaimSource.claim_id == claim.id)
                    )
                    or 0
                )
                assert links == 0, "an unsupported claim must cite nothing"

    async def test_the_report_states_its_provenance_and_limitations(
        self, node_deps: NodeDeps
    ) -> None:
        """Generated from run data, so it cannot overstate what happened."""
        task_id = await create_task_row(node_deps)
        await run_to_gate(node_deps, task_id, InMemorySaver())

        async with node_deps.session_factory() as session:
            report = await session.scalar(
                select(Report).where(Report.task_id == uuid.UUID(task_id))
            )
        assert report is not None
        markdown = report.content_markdown

        assert "not live AI research" in markdown
        assert "## Limitations" in markdown
        assert "deterministic stub adapter" in markdown
        assert "fixture corpus" in markdown
        assert "not** a fact check" in markdown
        assert "could not be supported" in markdown

    async def test_the_ledger_records_every_model_call(self, node_deps: NodeDeps) -> None:
        task_id = await create_task_row(node_deps)
        await run_to_gate(node_deps, task_id, InMemorySaver())

        async with node_deps.session_factory() as session:
            calls = (
                await session.scalars(select(LLMCall).where(LLMCall.task_id == uuid.UUID(task_id)))
            ).all()
            task = await session.get(ResearchTask, uuid.UUID(task_id))

        # One call per LLM node; `research` queries the search port instead.
        assert {c.node for c in calls} == set(LLM_NODES)
        assert task is not None
        assert task.calls_used == len(calls)
        assert task.tokens_used == sum(c.tokens_in + c.tokens_out for c in calls)


class TestDeterminism:
    async def test_two_runs_of_the_same_question_agree(self, node_deps: NodeDeps) -> None:
        """The offline demo is only meaningful if it is repeatable."""
        reports = []
        for _ in range(2):
            task_id = await create_task_row(node_deps)
            await run_to_gate(node_deps, task_id, InMemorySaver())
            async with node_deps.session_factory() as session:
                report = await session.scalar(
                    select(Report).where(Report.task_id == uuid.UUID(task_id))
                )
                assert report is not None
                # Spend lines differ only by counters, which are identical here.
                reports.append(report.content_markdown)
        assert reports[0] == reports[1]


class TestApprovalAndRevision:
    async def test_approving_finishes_the_run(self, node_deps: NodeDeps) -> None:
        task_id = await create_task_row(node_deps)
        checkpointer = InMemorySaver()
        graph = await run_to_gate(node_deps, task_id, checkpointer)
        config = {"configurable": {"thread_id": task_id}}

        await graph.ainvoke(Command(resume={"action": ApprovalAction.APPROVE.value}), config=config)
        state = await graph.aget_state(config)
        assert not state.next, "an approved run should be finished"

    async def test_a_revision_produces_a_second_version(self, node_deps: NodeDeps) -> None:
        task_id = await create_task_row(node_deps)
        checkpointer = InMemorySaver()
        graph = await run_to_gate(node_deps, task_id, checkpointer)
        config = {"configurable": {"thread_id": task_id}}

        await graph.ainvoke(
            Command(
                resume={
                    "action": ApprovalAction.REQUEST_REVISION.value,
                    "feedback": "Please say more about licensing.",
                }
            ),
            config=config,
        )

        async with node_deps.session_factory() as session:
            reports = (
                await session.scalars(
                    select(Report)
                    .where(Report.task_id == uuid.UUID(task_id))
                    .order_by(Report.version)
                )
            ).all()

        assert [r.version for r in reports] == [1, 2]
        assert reports[0].status is ReportStatus.SUPERSEDED
        assert reports[0].superseded_by == reports[1].id
        assert reports[1].status is ReportStatus.DRAFT

    async def test_a_revision_re_runs_nodes_as_new_attempts(self, node_deps: NodeDeps) -> None:
        """History must accumulate: a revision cannot overwrite the first pass."""
        task_id = await create_task_row(node_deps)
        checkpointer = InMemorySaver()
        graph = await run_to_gate(node_deps, task_id, checkpointer)
        config = {"configurable": {"thread_id": task_id}}

        await graph.ainvoke(
            Command(
                resume={
                    "action": ApprovalAction.REQUEST_REVISION.value,
                    "feedback": "More on licensing please.",
                }
            ),
            config=config,
        )

        async with node_deps.session_factory() as session:
            attempts = (
                await session.scalars(
                    select(WorkflowStep.attempt).where(
                        WorkflowStep.task_id == uuid.UUID(task_id),
                        WorkflowStep.node == NodeName.DRAFT,
                    )
                )
            ).all()
        assert sorted(attempts) == [1, 2]

    async def test_a_revision_pauses_at_the_gate_again(self, node_deps: NodeDeps) -> None:
        task_id = await create_task_row(node_deps)
        checkpointer = InMemorySaver()
        graph = await run_to_gate(node_deps, task_id, checkpointer)
        config = {"configurable": {"thread_id": task_id}}

        await graph.ainvoke(
            Command(
                resume={
                    "action": ApprovalAction.REQUEST_REVISION.value,
                    "feedback": "Expand the licensing section.",
                }
            ),
            config=config,
        )
        state = await graph.aget_state(config)
        assert state.next, "a revised report must wait for approval too"


def _provenance(**overrides: object) -> ReportProvenance:
    base: dict[str, object] = {
        "search_mode": SearchMode.FIXTURE,
        "llm_provider": LLMProvider.STUB,
        "models_served": ("vendor/model",),
        "source_count": 8,
        "subtask_count": 3,
        "tokens_used": 1000,
        "calls_used": 4,
        "version": 1,
        "revision_count": 0,
        "live_source_count": 0,
        "fixture_source_count": 0,
        "search_failures": 0,
    }
    base.update(overrides)
    return ReportProvenance(**base)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("overrides", "must_contain", "must_not_contain"),
    [
        # Fully offline: a stub wrote it over synthetic sources.
        (
            {"fixture_source_count": 8},
            "No language model was called",
            "Real model output",
        ),
        # A real model reasoning over fixture sources must not be described
        # as "no language model was called" -- the M3 defect.
        (
            {"llm_provider": LLMProvider.OPENROUTER, "fixture_source_count": 8},
            "Real model output over fixture sources",
            "No language model was called",
        ),
        # Stub prose over genuinely retrieved pages.
        (
            {"search_mode": SearchMode.TAVILY, "live_source_count": 8},
            "Fixture text over real sources",
            "No language model was called",
        ),
        # Part live, part fixture: describing this by configuration alone
        # would be inaccurate whichever mode was configured.
        (
            {
                "llm_provider": LLMProvider.OPENROUTER,
                "search_mode": SearchMode.TAVILY,
                "live_source_count": 5,
                "fixture_source_count": 3,
            },
            "Mixed evidence",
            "No language model was called",
        ),
        # Fully live: nothing to disclaim about provenance, everything to
        # disclaim about reliability.
        (
            {
                "llm_provider": LLMProvider.OPENROUTER,
                "search_mode": SearchMode.TAVILY,
                "live_source_count": 8,
            },
            "retrieved, not verified",
            "fixture",
        ),
    ],
)
def test_the_provenance_notice_matches_what_actually_ran(
    overrides: dict[str, object], must_contain: str, must_not_contain: str
) -> None:
    """Regression test for a false provenance claim.

    The notice originally fired when the provider was a stub *or* search was
    fixtures, so a live model over fixture sources produced a report stating
    "No language model was called" while its own limitations named the model
    that wrote it. It is now derived from the sources actually stored.
    """
    notice = provenance_notice(_provenance(**overrides))
    assert must_contain in notice
    assert must_not_contain.lower() not in notice.lower()


def test_a_fully_live_report_still_disclaims_reliability() -> None:
    """A page being retrievable says nothing about whether it is correct, so
    even a fully live run carries a notice."""
    notice = provenance_notice(
        _provenance(
            llm_provider=LLMProvider.OPENROUTER,
            search_mode=SearchMode.TAVILY,
            live_source_count=8,
        )
    )
    assert "retrieved, not verified" in notice
    assert "does not assess" in notice.lower() or "not assess" in notice.lower()


def test_a_notice_is_always_produced() -> None:
    """Returning None for any combination would leave a report with no
    provenance statement at all."""
    for overrides in (
        {},
        {"live_source_count": 1},
        {"fixture_source_count": 1},
        {"live_source_count": 1, "fixture_source_count": 1},
        {"llm_provider": LLMProvider.OPENROUTER},
    ):
        assert provenance_notice(_provenance(**overrides)).startswith(">")


# ---------------------------------------------------------------------------
# Partial research: retrieval failing for some subtasks but not all
# ---------------------------------------------------------------------------


class FlakySearchProvider:
    """Fails for chosen query indices, succeeds for the rest."""

    def __init__(self, inner: Any, fail_indices: set[int]) -> None:
        self._inner = inner
        self._fail = fail_indices
        self._calls = 0

    @property
    def mode(self) -> SearchMode:
        return self._inner.mode

    async def search(self, query: str, *, max_results: int) -> Any:
        index = self._calls
        self._calls += 1
        if index in self._fail:
            raise ProviderUnavailableError("search provider is down")
        return await self._inner.search(query, max_results=max_results)

    async def aclose(self) -> None:
        await self._inner.aclose()


@pytest.mark.integration
class TestPartialResearch:
    async def test_one_failed_subtask_still_produces_a_report(self, node_deps: NodeDeps) -> None:
        """Partial research is a result, not a dead task: the remaining
        subtasks may still carry usable evidence."""
        deps = NodeDeps(
            settings=node_deps.settings,
            session_factory=node_deps.session_factory,
            llm=node_deps.llm,
            search=FlakySearchProvider(node_deps.search, {0}),
        )
        task_id = await create_task_row(deps)
        await run_to_gate(deps, task_id, InMemorySaver())

        async with deps.session_factory() as session:
            report = await session.scalar(
                select(Report).where(Report.task_id == uuid.UUID(task_id))
            )
            step = await session.scalar(
                select(WorkflowStep).where(
                    WorkflowStep.task_id == uuid.UUID(task_id),
                    WorkflowStep.node == NodeName.RESEARCH,
                )
            )

        assert report is not None
        assert step is not None
        assert step.output_json is not None
        assert step.output_json["partial"] is True
        assert step.output_json["subtasks_failed"] == 1

    async def test_the_report_discloses_that_research_was_partial(
        self, node_deps: NodeDeps
    ) -> None:
        """A reader must not have to infer that evidence is missing."""
        deps = NodeDeps(
            settings=node_deps.settings,
            session_factory=node_deps.session_factory,
            llm=node_deps.llm,
            search=FlakySearchProvider(node_deps.search, {0}),
        )
        task_id = await create_task_row(deps)
        await run_to_gate(deps, task_id, InMemorySaver())

        async with deps.session_factory() as session:
            report = await session.scalar(
                select(Report).where(Report.task_id == uuid.UUID(task_id))
            )
        assert report is not None
        assert "Retrieval failed for 1 subtask" in report.content_markdown
        assert "partial research" in report.content_markdown

    async def test_every_subtask_failing_aborts_rather_than_drafting(
        self, node_deps: NodeDeps
    ) -> None:
        """With no evidence at all, drafting would produce a report grounded
        in nothing."""
        deps = NodeDeps(
            settings=node_deps.settings,
            session_factory=node_deps.session_factory,
            llm=node_deps.llm,
            search=FlakySearchProvider(node_deps.search, {0, 1, 2, 3, 4, 5, 6, 7}),
        )
        task_id = await create_task_row(deps)

        with pytest.raises(SearchUnavailableError):
            await run_to_gate(deps, task_id, InMemorySaver())

        async with deps.session_factory() as session:
            report = await session.scalar(
                select(Report).where(Report.task_id == uuid.UUID(task_id))
            )
            step = await session.scalar(
                select(WorkflowStep).where(
                    WorkflowStep.task_id == uuid.UUID(task_id),
                    WorkflowStep.node == NodeName.RESEARCH,
                )
            )
        assert report is None, "no report may be written without evidence"
        assert step is not None
        assert step.status is StepStatus.FAILED

    async def test_the_per_task_search_budget_truncates_the_plan(self, node_deps: NodeDeps) -> None:
        """A free allowance is shared and exhaustible: a runaway plan should
        cost one task, not the month."""
        from app.graph.nodes.research import research_node

        deps = NodeDeps(
            settings=make_settings(search_max_per_task=2),
            session_factory=node_deps.session_factory,
            llm=node_deps.llm,
            search=node_deps.search,
        )
        task_id = await create_task_row(deps)
        delta = await research_node(
            {
                "task_id": task_id,
                "question": QUESTION,
                # Real queries: the fixture tokeniser discards tokens
                # shorter than three characters, so single letters retrieve
                # nothing and would test the wrong thing.
                "search_queries": [
                    "airflow operational burden",
                    "prefect developer experience",
                    "dagster assets testing",
                    "licensing open core",
                    "comparison throughput benchmarks",
                ],
                "subtasks": ["one", "two", "three", "four", "five"],
            },
            deps,
        )
        assert delta["source_ids"]

        async with deps.session_factory() as session:
            step = await session.scalar(
                select(WorkflowStep).where(
                    WorkflowStep.task_id == uuid.UUID(task_id),
                    WorkflowStep.node == NodeName.RESEARCH,
                )
            )
        assert step is not None
        assert step.output_json is not None
        assert len(step.output_json["queries"]) == 2
