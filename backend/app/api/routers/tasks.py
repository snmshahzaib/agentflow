"""Research task endpoints.

The API writes rows and enqueues work. It never calls a model: a research run
takes minutes, and a request handler must not be holding that (ADR-001).
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Query, Response, status

from app.api.deps import SessionDep, TaskServiceDep
from app.core.logging import get_logger
from app.domain.enums import ApprovalAction
from app.schemas.common import ErrorResponse, Page
from app.schemas.task import (
    ApprovalRequest,
    ApprovalResult,
    StepView,
    TaskCreate,
    TaskDetail,
    TaskSummary,
)

log = get_logger(__name__)

router = APIRouter(prefix="/api/tasks", tags=["tasks"])

_ERRORS: dict[int | str, dict[str, object]] = {
    404: {"model": ErrorResponse, "description": "No such task for this session."},
    409: {"model": ErrorResponse, "description": "The task is in an incompatible state."},
    422: {"model": ErrorResponse, "description": "The request was not valid."},
}


@router.post(
    "",
    response_model=TaskDetail,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Submit a research task",
    responses=_ERRORS,
)
async def create_task(
    payload: TaskCreate,
    service: TaskServiceDep,
    session_id: SessionDep,
    response: Response,
    idempotency_key: Annotated[
        str | None,
        Header(
            alias="Idempotency-Key",
            description="Repeat this header to retry a submission without creating a duplicate.",
            max_length=128,
        ),
    ] = None,
) -> TaskDetail:
    """Create a task and queue it. Returns 202: the work has not run yet."""
    task = await service.create(
        session_id=session_id,
        question=payload.question,
        constraints=payload.constraints,
        idempotency_key=idempotency_key,
    )
    response.headers["Location"] = f"/api/tasks/{task.id}"
    return TaskDetail.model_validate(task)


@router.get("", response_model=Page[TaskSummary], summary="List this session's tasks")
async def list_tasks(
    service: TaskServiceDep,
    session_id: SessionDep,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    cursor: str | None = None,
) -> Page[TaskSummary]:
    tasks, next_cursor = await service.list_for_session(
        session_id=session_id, limit=limit, cursor=cursor
    )
    return Page[TaskSummary](
        items=[TaskSummary.model_validate(t) for t in tasks], next_cursor=next_cursor
    )


@router.get("/{task_id}", response_model=TaskDetail, summary="Task detail", responses=_ERRORS)
async def get_task(task_id: UUID, service: TaskServiceDep, session_id: SessionDep) -> TaskDetail:
    task = await service.get(task_id=task_id, session_id=session_id)
    return TaskDetail.model_validate(task)


@router.get(
    "/{task_id}/steps",
    response_model=list[StepView],
    summary="Workflow timeline",
    responses=_ERRORS,
)
async def get_steps(
    task_id: UUID, service: TaskServiceDep, session_id: SessionDep
) -> list[StepView]:
    """Every attempt at every node, oldest first — failures included, so a
    retry is visible rather than hidden."""
    steps = await service.steps(task_id=task_id, session_id=session_id)
    return [StepView.model_validate(s) for s in steps]


@router.post(
    "/{task_id}/approve",
    response_model=ApprovalResult,
    summary="Approve the current report",
    responses=_ERRORS,
)
async def approve_task(
    task_id: UUID,
    payload: ApprovalRequest,
    service: TaskServiceDep,
    session_id: SessionDep,
) -> ApprovalResult:
    """Record approval and queue the graph's continuation.

    Only a task awaiting approval can be approved; anything else is a 409.
    """
    task, version = await service.approve(
        task_id=task_id, session_id=session_id, feedback=payload.feedback
    )
    return ApprovalResult(
        task_id=task.id,
        action=ApprovalAction.APPROVE,
        status=task.status,
        report_version=version,
    )
