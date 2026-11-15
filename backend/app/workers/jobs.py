"""Worker jobs.

A task's rows are written only by the worker holding its single-flight lock,
so there is exactly one writer per task. That lock plus
``UNIQUE(task_id, node, attempt)`` is what makes a re-delivered job harmless.
"""

from __future__ import annotations

import uuid
from typing import Any

from langgraph.types import Command

from app.core.correlation import new_correlation_id, set_correlation_id, set_task_id
from app.core.logging import get_logger
from app.db.models import ResearchTask
from app.domain.enums import TaskStatus
from app.graph.builder import build_graph
from app.workers.locks import single_flight

log = get_logger(__name__)

#: How long a task may hold its lock before it is considered abandoned. Long
#: enough for a full graph run, short enough that a crashed worker's task
#: becomes retryable without manual intervention.
LOCK_TTL_SECONDS = 900


async def _set_status(ctx: dict[str, Any], task_id: str, **changes: Any) -> None:
    factory = ctx["session_factory"]
    async with factory() as session, session.begin():
        task = await session.get(ResearchTask, uuid.UUID(task_id))
        if task is None:
            log.warning("task_missing_on_status_update", task_id=task_id)
            return
        for field, value in changes.items():
            setattr(task, field, value)


async def run_task(ctx: dict[str, Any], task_id: str) -> str:
    """Execute a research task up to the approval gate.

    Returns the status reached. The job ends at the interrupt: nothing waits
    on the human, and a restart of every process loses no progress.
    """
    set_correlation_id(new_correlation_id())
    set_task_id(task_id)

    async with single_flight(ctx["redis"], f"task:{task_id}", ttl=LOCK_TTL_SECONDS) as acquired:
        if not acquired:
            # Another worker holds this task. Duplicate delivery is expected,
            # not exceptional.
            log.info("task_already_running", task_id=task_id)
            return "locked"

        factory = ctx["session_factory"]
        async with factory() as session:
            task = await session.get(ResearchTask, uuid.UUID(task_id))
            if task is None:
                log.warning("task_not_found", task_id=task_id)
                return "missing"
            if task.status.is_terminal or task.status is TaskStatus.AWAITING_APPROVAL:
                log.info("task_not_runnable", task_id=task_id, status=task.status.value)
                return str(task.status.value)
            question = task.question
            constraints = dict(task.constraints_json)

        await _set_status(ctx, task_id, status=TaskStatus.RUNNING)

        graph = build_graph(ctx["node_deps"], checkpointer=ctx["checkpointer"])
        config = {"configurable": {"thread_id": task_id}}

        try:
            await graph.ainvoke(
                {"task_id": task_id, "question": question, "constraints": constraints},
                config=config,
            )
        except Exception as exc:
            log.exception("task_failed", task_id=task_id, error=type(exc).__name__)
            await _set_status(
                ctx,
                task_id,
                status=TaskStatus.FAILED,
                error_code=getattr(exc, "code", "internal_error"),
                error_message=str(exc)[:1000],
            )
            raise

        # Reaching here means the graph hit the approval interrupt.
        state = await graph.aget_state(config)
        if state.next:
            await _set_status(ctx, task_id, status=TaskStatus.AWAITING_APPROVAL)
            log.info("task_awaiting_approval", task_id=task_id)
            return TaskStatus.AWAITING_APPROVAL.value

        await _set_status(ctx, task_id, status=TaskStatus.APPROVED)
        return TaskStatus.APPROVED.value


async def resume_task(ctx: dict[str, Any], task_id: str, decision: dict[str, Any]) -> str:
    """Continue a task paused at the approval gate."""
    set_correlation_id(new_correlation_id())
    set_task_id(task_id)

    async with single_flight(ctx["redis"], f"task:{task_id}", ttl=LOCK_TTL_SECONDS) as acquired:
        if not acquired:
            log.info("task_already_running", task_id=task_id)
            return "locked"

        graph = build_graph(ctx["node_deps"], checkpointer=ctx["checkpointer"])
        config = {"configurable": {"thread_id": task_id}}

        try:
            await graph.ainvoke(Command(resume=decision), config=config)
        except Exception as exc:
            log.exception("task_resume_failed", task_id=task_id, error=type(exc).__name__)
            await _set_status(
                ctx,
                task_id,
                status=TaskStatus.FAILED,
                error_code=getattr(exc, "code", "internal_error"),
                error_message=str(exc)[:1000],
            )
            raise

        log.info("task_resumed", task_id=task_id, action=decision.get("action"))
        return "resumed"
