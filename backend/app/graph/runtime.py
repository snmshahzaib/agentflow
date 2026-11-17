"""Shared node machinery.

Every node persists a step row, every model call is budget-checked before it
happens and recorded in the ledger afterwards, and both are done the same way
so no node can quietly skip one.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select

from app.adapters.llm.base import CallUsage
from app.core.logging import get_logger
from app.db.models import LLMCall, ResearchTask, WorkflowStep
from app.domain.enums import StepStatus, TaskStatus
from app.domain.errors import BudgetExhaustedError, NotFoundError
from app.domain.nodes import NodeName
from app.graph.deps import NodeDeps

log = get_logger(__name__)


@dataclass
class StepRecord:
    """Filled in by a node; written to the database when the block exits."""

    output: dict[str, Any] = field(default_factory=dict)
    input_summary: str = ""


async def _next_attempt(deps: NodeDeps, task_id: uuid.UUID, node: NodeName) -> int:
    async with deps.session_factory() as session:
        latest = await session.scalar(
            select(WorkflowStep.attempt)
            .where(WorkflowStep.task_id == task_id, WorkflowStep.node == node)
            .order_by(WorkflowStep.attempt.desc())
            .limit(1)
        )
    return int(latest or 0) + 1


@asynccontextmanager
async def record_step(deps: NodeDeps, *, task_id: str, node: NodeName) -> AsyncIterator[StepRecord]:
    """Record one attempt at ``node``, succeeded or failed.

    A failure is persisted too: the timeline must show that something was
    tried and did not work, rather than silently omitting it.
    """
    task_uuid = uuid.UUID(task_id)
    attempt = await _next_attempt(deps, task_uuid, node)
    record = StepRecord()
    started = time.perf_counter()

    try:
        yield record
    except Exception as exc:
        duration_ms = int((time.perf_counter() - started) * 1000)
        async with deps.session_factory() as session, session.begin():
            session.add(
                WorkflowStep(
                    task_id=task_uuid,
                    node=node,
                    status=StepStatus.FAILED,
                    attempt=attempt,
                    input_summary=record.input_summary or None,
                    error=f"{type(exc).__name__}: {exc}"[:2000],
                    duration_ms=duration_ms,
                )
            )
        log.warning("node_failed", node=node.value, attempt=attempt, error=type(exc).__name__)
        raise

    duration_ms = int((time.perf_counter() - started) * 1000)
    async with deps.session_factory() as session, session.begin():
        session.add(
            WorkflowStep(
                task_id=task_uuid,
                node=node,
                status=StepStatus.SUCCEEDED,
                attempt=attempt,
                input_summary=record.input_summary or None,
                output_json=record.output or None,
                duration_ms=duration_ms,
            )
        )
        task = await session.get(ResearchTask, task_uuid)
        if task is not None:
            task.current_node = node
            if task.status is TaskStatus.QUEUED:
                task.status = TaskStatus.RUNNING
    log.info("node_completed", node=node.value, attempt=attempt, duration_ms=duration_ms)


async def call_model[TOut: BaseModel](
    deps: NodeDeps,
    *,
    task_id: str,
    node: NodeName,
    schema: type[TOut],
    system: str,
    user: str,
    attempt: int = 1,
) -> TOut:
    """Make one budget-checked, ledgered model call.

    The budget is checked *before* calling, so an exhausted task fails cleanly
    instead of overrunning and leaving a half-written report.
    """
    task_uuid = uuid.UUID(task_id)

    async with deps.session_factory() as session:
        task = await session.get(ResearchTask, task_uuid)
        if task is None:
            raise NotFoundError("Research task disappeared mid-run.", task_id=task_id)
        if task.calls_used >= task.max_calls_budget:
            raise BudgetExhaustedError(
                f"Task reached its limit of {task.max_calls_budget} model calls.",
                calls_used=task.calls_used,
                node=node.value,
            )
        if task.tokens_used >= task.max_tokens_budget:
            raise BudgetExhaustedError(
                f"Task reached its limit of {task.max_tokens_budget} tokens.",
                tokens_used=task.tokens_used,
                node=node.value,
            )

    result = await deps.llm.complete_structured(
        node=node, schema=schema, system=system, user=user, attempt=attempt
    )
    await _record_usage(deps, task_uuid, result.usage)
    return result.output


async def _record_usage(deps: NodeDeps, task_id: uuid.UUID, usage: CallUsage) -> None:
    """Append to the ledger and advance the task's counters in one transaction."""
    async with deps.session_factory() as session, session.begin():
        session.add(
            LLMCall(
                task_id=task_id,
                node=usage.node,
                attempt=usage.attempt,
                provider=usage.provider,
                model_requested=usage.model_requested,
                model_served=usage.model_served,
                output_mode=usage.output_mode,
                generation_id=usage.generation_id,
                provider_served=usage.provider_served,
                tokens_in=usage.tokens_in,
                tokens_out=usage.tokens_out,
                cache_read_tokens=usage.cache_read_tokens,
                latency_ms=usage.latency_ms,
                cost_usd_estimate=usage.cost_usd_estimate,
                cost_usd_reported=usage.cost_usd_reported,
                attempt_outcome=usage.attempt_outcome,
            )
        )
        task = await session.get(ResearchTask, task_id)
        if task is not None:
            task.calls_used += 1
            task.tokens_used += usage.total_tokens
            if usage.cost_usd_estimate is not None:
                task.cost_usd_estimate = float(task.cost_usd_estimate) + usage.cost_usd_estimate
