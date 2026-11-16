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
from app.domain.enums import ApprovalAction, ReportStatus, StepStatus, SupportStatus
from app.domain.nodes import LLM_NODES, NODE_ORDER, NodeName
from app.graph.builder import build_graph
from app.graph.deps import NodeDeps
from tests.conftest import create_task_row

pytestmark = pytest.mark.integration

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
