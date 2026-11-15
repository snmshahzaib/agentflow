"""Graph assembly and node behaviour.

The graph is the part of M1 that the live smoke test exercises but unit tests
did not, so these cover the pieces that can be tested without a worker: that
the graph compiles with every node wired in the right order, and that a node
records the timeline transactionally.
"""

from __future__ import annotations

import uuid
from itertools import pairwise

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.llm.stub import StubLLMClient
from app.core.config import Settings
from app.db.models import ResearchTask, WorkflowStep
from app.domain.enums import LLMProvider, SearchMode, StepStatus, TaskStatus
from app.domain.nodes import NODE_ORDER, NodeName
from app.graph.builder import AWAIT_APPROVAL, build_graph
from app.graph.deps import NodeDeps
from app.graph.nodes.skeleton import SKELETON_MARKER, plan_node, research_node
from tests.conftest import make_settings


class TestGraphAssembly:
    """Also guards the one type: ignore in builder.py — LangGraph's stubs
    reject an async node callable that its runtime accepts, so if that
    changes, these fail loudly rather than silently."""

    def test_the_graph_compiles_with_every_node(self) -> None:
        deps = NodeDeps(
            settings=make_settings(),
            session_factory=None,  # type: ignore[arg-type]
            llm=StubLLMClient({}),
        )
        nodes = set(build_graph(deps).get_graph().nodes)
        for node in NODE_ORDER:
            assert node.value in nodes
        assert AWAIT_APPROVAL in nodes

    def test_nodes_run_in_the_documented_order(self) -> None:
        deps = NodeDeps(
            settings=make_settings(),
            session_factory=None,  # type: ignore[arg-type]
            llm=StubLLMClient({}),
        )
        edges = {(edge.source, edge.target) for edge in build_graph(deps).get_graph().edges}
        expected = list(pairwise(NODE_ORDER))
        for source, target in expected:
            assert (source.value, target.value) in edges
        assert (NodeName.DRAFT.value, AWAIT_APPROVAL) in edges

    def test_the_graph_can_be_built_without_a_checkpointer(self) -> None:
        """Tests and local experiments need an in-memory graph."""
        deps = NodeDeps(
            settings=make_settings(),
            session_factory=None,  # type: ignore[arg-type]
            llm=StubLLMClient({}),
        )
        assert build_graph(deps, checkpointer=None) is not None


@pytest.mark.integration
class TestSkeletonNodes:
    @pytest.fixture
    def deps(self, migrated_database: str) -> NodeDeps:
        from sqlalchemy.ext.asyncio import create_async_engine

        engine = create_async_engine(migrated_database)
        return NodeDeps(
            settings=make_settings(),
            session_factory=async_sessionmaker(engine, expire_on_commit=False),
            llm=StubLLMClient({}),
        )

    async def _task(self, session: AsyncSession, settings: Settings) -> ResearchTask:
        task = ResearchTask(
            session_id="sess-" + uuid.uuid4().hex[:8],
            question="Compare three workflow orchestration frameworks.",
            search_mode=SearchMode.FIXTURE,
            llm_provider=LLMProvider.STUB,
            max_tokens_budget=settings.llm_max_tokens_per_task,
            max_calls_budget=settings.llm_max_calls_per_task,
        )
        session.add(task)
        await session.commit()
        return task

    async def test_a_node_records_a_step_and_advances_the_task(self, deps: NodeDeps) -> None:
        async with deps.session_factory() as session:
            task = await self._task(session, deps.settings)
            task_id = str(task.id)

        delta = await plan_node({"task_id": task_id, "question": "q" * 20}, deps)
        assert delta["subtasks"]

        async with deps.session_factory() as session:
            step = await session.scalar(
                select(WorkflowStep).where(WorkflowStep.task_id == uuid.UUID(task_id))
            )
            assert step is not None
            assert step.node is NodeName.PLAN
            assert step.status is StepStatus.SUCCEEDED
            assert step.attempt == 1
            # M1 output must be self-identifying so no screenshot can present
            # it as a finished research workflow.
            assert step.output_json is not None
            assert step.output_json["mode"] == SKELETON_MARKER

            refreshed = await session.get(ResearchTask, uuid.UUID(task_id))
            assert refreshed is not None
            assert refreshed.current_node is NodeName.PLAN
            assert refreshed.status is TaskStatus.RUNNING

    async def test_the_plan_is_bounded_by_configuration(self, deps: NodeDeps) -> None:
        async with deps.session_factory() as session:
            task = await self._task(session, deps.settings)
            task_id = str(task.id)
        delta = await plan_node({"task_id": task_id, "question": "q" * 20}, deps)
        assert len(delta["subtasks"]) <= deps.settings.max_subtasks

    async def test_rerunning_a_node_records_a_new_attempt(self, deps: NodeDeps) -> None:
        """A retry must stay visible in the timeline, not overwrite history."""
        async with deps.session_factory() as session:
            task = await self._task(session, deps.settings)
            task_id = str(task.id)

        await research_node({"task_id": task_id, "subtasks": ["a"]}, deps)
        await research_node({"task_id": task_id, "subtasks": ["a"]}, deps)

        async with deps.session_factory() as session:
            attempts = await session.scalars(
                select(WorkflowStep.attempt)
                .where(
                    WorkflowStep.task_id == uuid.UUID(task_id),
                    WorkflowStep.node == NodeName.RESEARCH,
                )
                .order_by(WorkflowStep.attempt)
            )
            assert list(attempts.all()) == [1, 2]

    async def test_the_research_node_makes_no_model_call(self, deps: NodeDeps) -> None:
        """The model is never given network access; retrieval is our code's
        job. A stub with no registered responses would raise if called."""
        async with deps.session_factory() as session:
            task = await self._task(session, deps.settings)
            task_id = str(task.id)
        await research_node({"task_id": task_id, "subtasks": ["a"]}, deps)

        async with deps.session_factory() as session:
            count = await session.scalar(
                select(func.count())
                .select_from(WorkflowStep)
                .where(WorkflowStep.task_id == uuid.UUID(task_id))
            )
            assert count == 1
