"""Worker jobs.

A task's rows are written only by the worker holding its single-flight lock,
so there is exactly one writer per task. That lock plus
``UNIQUE(task_id, node, attempt)`` is what makes a re-delivered job harmless.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from langgraph.types import Command
from sqlalchemy import select

from app.core.correlation import new_correlation_id, set_correlation_id, set_task_id
from app.core.logging import get_logger
from app.db.models import ResearchTask
from app.domain.enums import TaskStatus
from app.graph.builder import build_graph
from app.services.task_service import purge_tasks_older_than
from app.workers.locks import is_locked, single_flight

log = get_logger(__name__)

#: Lock lifetime. Deliberately short and refreshed by a heartbeat while the
#: worker is alive: a SIGKILLed worker never releases its lock, so a long TTL
#: would strand its task for that whole duration.
LOCK_TTL_SECONDS = 180
#: How often the holder extends the TTL.
LOCK_HEARTBEAT_SECONDS = 60

#: A task must look stalled for at least this long before the sweep adopts it,
#: so a task between enqueue and claim is never stolen from a healthy worker.
ORPHAN_GRACE_SECONDS = 120


async def _set_status(ctx: dict[str, Any], task_id: str, **changes: Any) -> None:
    factory = ctx["session_factory"]
    async with factory() as session, session.begin():
        task = await session.get(ResearchTask, uuid.UUID(task_id))
        if task is None:
            log.warning("task_missing_on_status_update", task_id=task_id)
            return
        for field, value in changes.items():
            setattr(task, field, value)


async def _settle_status(
    ctx: dict[str, Any], graph: Any, config: dict[str, Any], task_id: str
) -> str:
    """Reconcile the task's status with where the graph actually is.

    Both entry points need this. A graph that is interrupted is waiting for a
    human, whatever the task row currently says; without this a revised task
    stays `running` forever even though its report is ready to review.
    """
    state = await graph.aget_state(config)
    if state.next:
        await _set_status(ctx, task_id, status=TaskStatus.AWAITING_APPROVAL)
        log.info("task_awaiting_approval", task_id=task_id)
        return TaskStatus.AWAITING_APPROVAL.value

    # The graph has finished. Approval is recorded by the API before the
    # resume is enqueued, so the status is already correct and must not be
    # overwritten here.
    log.info("task_graph_complete", task_id=task_id)
    return "complete"


async def run_task(ctx: dict[str, Any], task_id: str) -> str:
    """Execute a research task up to the approval gate.

    Returns the status reached. The job ends at the interrupt: nothing waits
    on the human, and a restart of every process loses no progress.
    """
    set_correlation_id(new_correlation_id())
    set_task_id(task_id)

    async with single_flight(
        ctx["redis"],
        f"task:{task_id}",
        ttl=LOCK_TTL_SECONDS,
        heartbeat_interval=LOCK_HEARTBEAT_SECONDS,
    ) as acquired:
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

        return await _settle_status(ctx, graph, config, task_id)


async def resume_task(ctx: dict[str, Any], task_id: str, decision: dict[str, Any]) -> str:
    """Continue a task paused at the approval gate."""
    set_correlation_id(new_correlation_id())
    set_task_id(task_id)

    async with single_flight(
        ctx["redis"],
        f"task:{task_id}",
        ttl=LOCK_TTL_SECONDS,
        heartbeat_interval=LOCK_HEARTBEAT_SECONDS,
    ) as acquired:
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
        # A revision re-enters the graph and parks at the gate again, so the
        # status has to be settled here too.
        await _settle_status(ctx, graph, config, task_id)
        return "resumed"


async def purge_expired_tasks(ctx: dict[str, Any]) -> int:
    """Delete visitor research content past the retention window.

    A public demo collects strangers' research questions, the pages retrieved
    for them and the reports written from them. Keeping that indefinitely is a
    privacy problem rather than an untidiness one, so it is deleted on a
    schedule instead of accumulating until someone remembers.

    Deliberately a hard delete: a soft delete would leave the questions and
    retrieved excerpts in the table, which is the part that matters here.
    Cascades remove steps, sources, reports, claims, evidence links,
    approvals and ledger rows with the task.

    Returns the number of tasks removed, so the log says what happened on a
    quiet day as well as a busy one.
    """
    set_correlation_id(new_correlation_id())
    settings = ctx["settings"]
    factory = ctx["session_factory"]

    async with factory() as session, session.begin():
        removed = await purge_tasks_older_than(session, retention_days=settings.data_retention_days)

    log.info(
        "retention_sweep_completed",
        removed=removed,
        retention_days=settings.data_retention_days,
    )
    return removed


async def recover_orphaned_tasks(ctx: dict[str, Any]) -> int:
    """Re-queue tasks whose worker died without finishing them.

    A worker killed abruptly leaves no trace except a task row stuck in
    `queued` or `running` and a lock that expires on its own. This sweep finds
    those tasks once the lock is gone and puts them back on the queue, so a
    hard kill costs a retry rather than a permanently stranded task.

    A task whose lock is still present belongs to a live worker and is left
    alone; the grace period keeps a freshly-enqueued task from being adopted
    before its worker has had a chance to claim it.
    """
    set_correlation_id(new_correlation_id())
    redis = ctx["redis"]
    factory = ctx["session_factory"]
    cutoff = datetime.now(UTC) - timedelta(seconds=ORPHAN_GRACE_SECONDS)

    async with factory() as session:
        candidates = list(
            (
                await session.scalars(
                    select(ResearchTask).where(
                        ResearchTask.status.in_([TaskStatus.QUEUED, TaskStatus.RUNNING]),
                        ResearchTask.updated_at < cutoff,
                        ResearchTask.deleted_at.is_(None),
                    )
                )
            ).all()
        )

    requeued = 0
    for task in candidates:
        task_id = str(task.id)
        if await is_locked(redis, f"task:{task_id}"):
            continue  # a live worker owns it
        await redis.enqueue_job("run_task", task_id)
        requeued += 1
        log.warning(
            "orphaned_task_requeued",
            task_id=task_id,
            status=task.status.value,
            stalled_since=task.updated_at.isoformat(),
        )

    if requeued:
        log.info("orphan_sweep_completed", candidates=len(candidates), requeued=requeued)
    return requeued
