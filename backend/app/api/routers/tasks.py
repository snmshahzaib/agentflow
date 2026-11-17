"""Research task endpoints.

The API writes rows and enqueues work. It never calls a model: a research run
takes minutes, and a request handler must not be holding that (ADR-001).
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Query, Response, status
from fastapi.responses import PlainTextResponse

from app.api.deps import RateLimited, SessionDep, TaskServiceDep
from app.core.logging import get_logger
from app.domain.enums import ApprovalAction
from app.graph.binding import label_for
from app.schemas.common import ErrorResponse, Page
from app.schemas.task import (
    ApprovalRequest,
    ApprovalResult,
    ClaimView,
    ReportView,
    RevisionRequest,
    SourceView,
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
    429: {
        "model": ErrorResponse,
        "description": "Rate limit exceeded; see the Retry-After header.",
    },
}


@router.post(
    "",
    response_model=TaskDetail,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Submit a research task",
    responses=_ERRORS,
    dependencies=[RateLimited],
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
    dependencies=[RateLimited],
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


@router.post(
    "/{task_id}/request-revision",
    response_model=ApprovalResult,
    summary="Send the report back for revision",
    responses=_ERRORS,
    dependencies=[RateLimited],
)
async def request_revision(
    task_id: UUID,
    payload: RevisionRequest,
    service: TaskServiceDep,
    session_id: SessionDep,
) -> ApprovalResult:
    """Record the request and resume the graph at the planning step.

    Re-planning rather than re-wording means the reviewer's note can change
    which sources are retrieved. Revisions are capped; exceeding the cap is a
    409 rather than a silent no-op.
    """
    task, version = await service.request_revision(
        task_id=task_id, session_id=session_id, feedback=payload.feedback
    )
    return ApprovalResult(
        task_id=task.id,
        action=ApprovalAction.REQUEST_REVISION,
        status=task.status,
        report_version=version,
    )


@router.get(
    "/{task_id}/sources",
    response_model=list[SourceView],
    summary="Sources retrieved for this task",
    responses=_ERRORS,
)
async def get_sources(
    task_id: UUID, service: TaskServiceDep, session_id: SessionDep
) -> list[SourceView]:
    """In label order: the first entry is the source the model saw as `S1`."""
    sources = await service.sources(task_id=task_id, session_id=session_id)
    return [
        SourceView(
            id=source.id,
            label=label_for(index),
            subtask_index=source.subtask_index,
            url=source.url,
            title=source.title,
            excerpt=source.excerpt,
            provider=source.provider,
            rank=source.rank,
            is_fixture=source.is_fixture,
            retrieved_at=source.retrieved_at,
        )
        for index, source in enumerate(sources)
    ]


@router.get(
    "/{task_id}/report",
    response_model=ReportView,
    summary="Report content",
    responses=_ERRORS,
)
async def get_report(
    task_id: UUID,
    service: TaskServiceDep,
    session_id: SessionDep,
    version: Annotated[int | None, Query(ge=1)] = None,
) -> ReportView:
    """The latest version, or a specific one. Readable before approval —
    approval gates *export*, not inspection."""
    report = await service.report(task_id=task_id, session_id=session_id, version=version)
    return ReportView.model_validate(report)


@router.get(
    "/{task_id}/claims",
    response_model=list[ClaimView],
    summary="Per-claim support verdicts",
    responses=_ERRORS,
)
async def get_claims(
    task_id: UUID,
    service: TaskServiceDep,
    session_id: SessionDep,
    version: Annotated[int | None, Query(ge=1)] = None,
) -> list[ClaimView]:
    """Whether each claim is backed by a retrieved source.

    This is claim-to-source support, not a fact check: a `supported` verdict
    means a retrieved source states the claim, not that the claim is true.
    """
    sources = await service.sources(task_id=task_id, session_id=session_id)
    label_by_id = {source.id: label_for(index) for index, source in enumerate(sources)}

    claims = await service.claims(task_id=task_id, session_id=session_id, version=version)
    return [
        ClaimView(
            id=claim.id,
            ordinal=claim.ordinal,
            claim_text=claim.claim_text,
            support_status=claim.support_status,
            rationale=claim.rationale,
            source_ids=source_ids,
            source_labels=[label_by_id[sid] for sid in source_ids if sid in label_by_id],
        )
        for claim, source_ids in claims
    ]


@router.get(
    "/{task_id}/export",
    response_class=PlainTextResponse,
    summary="Export the approved report as Markdown",
    responses={
        **_ERRORS,
        200: {"content": {"text/markdown": {}}, "description": "The approved report."},
    },
)
async def export_report(
    task_id: UUID, service: TaskServiceDep, session_id: SessionDep
) -> PlainTextResponse:
    """Refused with 409 until a human has approved the report."""
    markdown, version = await service.export_markdown(task_id=task_id, session_id=session_id)
    return PlainTextResponse(
        content=markdown,
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": (
                f'attachment; filename="agentflow-report-{task_id}-v{version}.md"'
            )
        },
    )
