"""M1 skeleton nodes.

These record the workflow timeline and prove the durable execution path end
to end — enqueue, claim, step persistence, interrupt, resume — without doing
the real research work. Each is replaced by its real implementation in M2.

They are deliberately honest about what they are: every step row records
``skeleton`` in its output so no screenshot or API response can present M1
output as a finished research workflow.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from sqlalchemy import select

from app.core.logging import get_logger
from app.db.models import ResearchTask, WorkflowStep
from app.domain.enums import StepStatus, TaskStatus
from app.domain.nodes import NodeName
from app.graph.deps import NodeDeps
from app.graph.state import GraphState

log = get_logger(__name__)

#: Marks every artefact produced by a skeleton node.
SKELETON_MARKER = "skeleton"


async def _record_step(
    deps: NodeDeps,
    *,
    task_id: str,
    node: NodeName,
    output: dict[str, Any],
    duration_ms: int,
) -> None:
    """Persist the step row and advance the task's current node.

    One transaction per node: the step row and the task update commit
    together, so the timeline can never show a node the task has not reached.
    """
    async with deps.session_factory() as session, session.begin():
        attempt = await session.scalar(
            select(WorkflowStep.attempt)
            .where(
                WorkflowStep.task_id == uuid.UUID(task_id),
                WorkflowStep.node == node,
            )
            .order_by(WorkflowStep.attempt.desc())
            .limit(1)
        )
        session.add(
            WorkflowStep(
                task_id=uuid.UUID(task_id),
                node=node,
                status=StepStatus.SUCCEEDED,
                attempt=(attempt or 0) + 1,
                input_summary=f"{SKELETON_MARKER}: no model call",
                output_json={"mode": SKELETON_MARKER, **output},
                duration_ms=duration_ms,
            )
        )
        task = await session.get(ResearchTask, uuid.UUID(task_id))
        if task is not None:
            task.current_node = node
            if task.status is TaskStatus.QUEUED:
                task.status = TaskStatus.RUNNING


async def plan_node(state: GraphState, deps: NodeDeps) -> GraphState:
    """Produce a bounded plan. M2 replaces this with a planner model call."""
    started = time.perf_counter()
    limit = deps.settings.max_subtasks
    subtasks = [f"Subtask {i + 1} for: {state['question'][:60]}" for i in range(min(2, limit))]
    await _record_step(
        deps,
        task_id=state["task_id"],
        node=NodeName.PLAN,
        output={"subtasks": subtasks, "limit": limit},
        duration_ms=int((time.perf_counter() - started) * 1000),
    )
    return {"subtasks": subtasks}


async def research_node(state: GraphState, deps: NodeDeps) -> GraphState:
    """Retrieve sources. M2 wires the search port; no LLM call here, ever."""
    started = time.perf_counter()
    await _record_step(
        deps,
        task_id=state["task_id"],
        node=NodeName.RESEARCH,
        output={"subtasks_considered": len(state.get("subtasks", []))},
        duration_ms=int((time.perf_counter() - started) * 1000),
    )
    return {"source_ids": []}


async def analyse_node(state: GraphState, deps: NodeDeps) -> GraphState:
    started = time.perf_counter()
    await _record_step(
        deps,
        task_id=state["task_id"],
        node=NodeName.ANALYSE,
        output={"sources_considered": len(state.get("source_ids", []))},
        duration_ms=int((time.perf_counter() - started) * 1000),
    )
    return {}


async def citation_check_node(state: GraphState, deps: NodeDeps) -> GraphState:
    """Adjudicate claim-to-source support. Never a truth judgement."""
    started = time.perf_counter()
    await _record_step(
        deps,
        task_id=state["task_id"],
        node=NodeName.CITATION_CHECK,
        output={"claims_checked": 0},
        duration_ms=int((time.perf_counter() - started) * 1000),
    )
    return {}


async def draft_node(state: GraphState, deps: NodeDeps) -> GraphState:
    """Write the report. M2 produces real markdown, claims and limitations."""
    started = time.perf_counter()
    await _record_step(
        deps,
        task_id=state["task_id"],
        node=NodeName.DRAFT,
        output={"report_version": 1},
        duration_ms=int((time.perf_counter() - started) * 1000),
    )
    return {"report_version": 1}
