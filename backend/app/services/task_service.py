"""Research task use cases.

The transaction boundary for everything the API does. The API process never
executes a research task: it writes rows and enqueues work, so a long model
call can never block a request handler (ADR-001).
"""

from __future__ import annotations

import base64
import binascii
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.queue.base import TaskQueue
from app.core.config import Settings
from app.core.logging import get_logger
from app.db.models import (
    ApprovalEvent,
    ClaimSource,
    Report,
    ReportClaim,
    ResearchTask,
    Source,
    WorkflowStep,
)
from app.domain.enums import ApprovalAction, ReportStatus, TaskStatus
from app.domain.errors import ApprovalRequiredError, ConflictError, NotFoundError, ValidationError

log = get_logger(__name__)

MAX_PAGE_SIZE = 50


def _encode_cursor(moment: datetime, task_id: uuid.UUID) -> str:
    """Opaque keyset cursor. Keyset, not offset, so pagination is stable
    while new tasks arrive."""
    return base64.urlsafe_b64encode(f"{moment.isoformat()}|{task_id}".encode()).decode()


def _decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        moment, _, task_id = raw.partition("|")
        return datetime.fromisoformat(moment), uuid.UUID(task_id)
    except (ValueError, binascii.Error) as exc:
        raise ValidationError("The pagination cursor is not valid.", cursor=cursor) from exc


class TaskService:
    """Use cases for creating, reading and approving research tasks."""

    def __init__(self, session: AsyncSession, settings: Settings, queue: TaskQueue) -> None:
        self._session = session
        self._settings = settings
        self._queue = queue

    # -- commands --------------------------------------------------------

    async def create(
        self,
        *,
        session_id: str,
        question: str,
        constraints: dict[str, object] | None = None,
        idempotency_key: str | None = None,
    ) -> ResearchTask:
        """Create a task and queue it.

        Budgets and provenance are captured from the resolved configuration
        now, so a later configuration change cannot retroactively alter what a
        running task was permitted to spend, nor what a finished report claims
        produced it.
        """
        if idempotency_key:
            existing = await self._session.scalar(
                select(ResearchTask).where(
                    ResearchTask.session_id == session_id,
                    ResearchTask.idempotency_key == idempotency_key,
                )
            )
            if existing is not None:
                log.info(
                    "task_create_idempotent_hit",
                    task_id=str(existing.id),
                    idempotency_key=idempotency_key,
                )
                return existing

        task = ResearchTask(
            session_id=session_id,
            idempotency_key=idempotency_key,
            question=question.strip(),
            constraints_json=dict(constraints or {}),
            status=TaskStatus.QUEUED,
            search_mode=self._settings.search_provider,
            llm_provider=self._settings.llm_provider,
            max_tokens_budget=self._settings.llm_max_tokens_per_task,
            max_calls_budget=self._settings.llm_max_calls_per_task,
        )
        # The checkpoint thread is the task itself, so a resume can always
        # find the right graph state without a second lookup table.
        self._session.add(task)
        await self._session.flush()
        task.graph_thread_id = str(task.id)
        await self._session.flush()

        await self._queue.enqueue_run(str(task.id))
        log.info("task_created", task_id=str(task.id), status=task.status.value)
        return task

    async def approve(
        self, *, task_id: uuid.UUID, session_id: str, feedback: str | None = None
    ) -> tuple[ResearchTask, int]:
        """Record approval and queue the graph's continuation.

        Only a task actually waiting at the approval gate may be approved;
        approving anything else would fabricate a decision about a report that
        does not exist yet.
        """
        task = await self.get(task_id=task_id, session_id=session_id)
        if task.status is not TaskStatus.AWAITING_APPROVAL:
            raise ConflictError(
                f"This task is {task.status.value}, so it cannot be approved. "
                "Only a task awaiting approval can be approved.",
                status=task.status.value,
            )

        version = await self._latest_report_version(task_id)
        self._session.add(
            ApprovalEvent(
                task_id=task_id,
                report_version=version,
                session_id=session_id,
                action=ApprovalAction.APPROVE,
                feedback=feedback,
            )
        )
        task.status = TaskStatus.APPROVED

        report = await self._session.scalar(
            select(Report).where(Report.task_id == task_id, Report.version == version).limit(1)
        )
        if report is not None:
            report.status = ReportStatus.APPROVED

        await self._session.flush()
        await self._queue.enqueue_resume(
            str(task_id), {"action": ApprovalAction.APPROVE.value, "feedback": feedback}
        )
        log.info("task_approved", task_id=str(task_id), report_version=version)
        return task, version

    # -- queries ---------------------------------------------------------

    async def get(self, *, task_id: uuid.UUID, session_id: str) -> ResearchTask:
        """Fetch a task owned by this session.

        A task belonging to another session is reported as missing rather than
        forbidden, so the endpoint does not confirm that an id exists.
        """
        task = await self._session.scalar(
            select(ResearchTask).where(
                ResearchTask.id == task_id,
                ResearchTask.session_id == session_id,
                ResearchTask.deleted_at.is_(None),
            )
        )
        if task is None:
            raise NotFoundError("No such research task.", task_id=str(task_id))
        return task

    async def list_for_session(
        self, *, session_id: str, limit: int = 20, cursor: str | None = None
    ) -> tuple[list[ResearchTask], str | None]:
        """Newest first, keyset-paginated."""
        size = max(1, min(limit, MAX_PAGE_SIZE))
        query = (
            select(ResearchTask)
            .where(
                ResearchTask.session_id == session_id,
                ResearchTask.deleted_at.is_(None),
            )
            .order_by(ResearchTask.created_at.desc(), ResearchTask.id.desc())
            .limit(size + 1)
        )
        if cursor:
            moment, last_id = _decode_cursor(cursor)
            # Row-value comparison, so the keyset matches the composite
            # index (created_at DESC, id) rather than filtering in Python.
            query = query.where(
                tuple_(ResearchTask.created_at, ResearchTask.id) < (moment, last_id)
            )

        rows = list((await self._session.scalars(query)).all())
        next_cursor: str | None = None
        if len(rows) > size:
            rows = rows[:size]
            tail = rows[-1]
            next_cursor = _encode_cursor(tail.created_at, tail.id)
        return rows, next_cursor

    async def steps(self, *, task_id: uuid.UUID, session_id: str) -> list[WorkflowStep]:
        """The workflow timeline, oldest first, including failed attempts."""
        await self.get(task_id=task_id, session_id=session_id)
        rows = await self._session.scalars(
            select(WorkflowStep)
            .where(WorkflowStep.task_id == task_id)
            .order_by(WorkflowStep.started_at, WorkflowStep.attempt)
        )
        return list(rows.all())

    async def _latest_report_version(self, task_id: uuid.UUID) -> int:
        version = await self._session.scalar(
            select(Report.version)
            .where(Report.task_id == task_id)
            .order_by(Report.version.desc())
            .limit(1)
        )
        return int(version or 1)

    async def request_revision(
        self, *, task_id: uuid.UUID, session_id: str, feedback: str
    ) -> tuple[ResearchTask, int]:
        """Send a report back for revision and resume the graph.

        Revisions are capped: each one re-plans and re-retrieves, so an
        unbounded loop would be an unbounded spend. The cap is reported as a
        conflict rather than silently ignored.
        """
        task = await self.get(task_id=task_id, session_id=session_id)
        if task.status is not TaskStatus.AWAITING_APPROVAL:
            raise ConflictError(
                f"This task is {task.status.value}, so a revision cannot be requested. "
                "Only a task awaiting approval can be revised.",
                status=task.status.value,
            )

        rounds = await self._revision_count(task_id)
        limit = self._settings.max_revision_rounds
        if rounds >= limit:
            raise ConflictError(
                f"This task has already been revised {rounds} time(s), which is the "
                f"configured limit of {limit}. Approve the current version or start a "
                "new task.",
                revisions_used=rounds,
                limit=limit,
            )

        version = await self._latest_report_version(task_id)
        self._session.add(
            ApprovalEvent(
                task_id=task_id,
                report_version=version,
                session_id=session_id,
                action=ApprovalAction.REQUEST_REVISION,
                feedback=feedback,
            )
        )
        task.status = TaskStatus.RUNNING
        await self._session.flush()

        await self._queue.enqueue_resume(
            str(task_id),
            {"action": ApprovalAction.REQUEST_REVISION.value, "feedback": feedback},
        )
        log.info(
            "revision_requested",
            task_id=str(task_id),
            report_version=version,
            round=rounds + 1,
        )
        return task, version

    async def sources(self, *, task_id: uuid.UUID, session_id: str) -> list[Source]:
        """Retrieved sources in the order used to label them for the model."""
        await self.get(task_id=task_id, session_id=session_id)
        rows = await self._session.scalars(
            select(Source)
            .where(Source.task_id == task_id)
            .order_by(Source.subtask_index, Source.rank, Source.id)
        )
        return list(rows.all())

    async def report(
        self, *, task_id: uuid.UUID, session_id: str, version: int | None = None
    ) -> Report:
        """The requested report version, or the latest if none is given."""
        await self.get(task_id=task_id, session_id=session_id)
        query = select(Report).where(Report.task_id == task_id)
        query = (
            query.where(Report.version == version)
            if version is not None
            else query.order_by(Report.version.desc()).limit(1)
        )
        report = await self._session.scalar(query)
        if report is None:
            raise NotFoundError(
                "No report exists for this task yet.",
                task_id=str(task_id),
                version=version,
            )
        return report

    async def claims(
        self, *, task_id: uuid.UUID, session_id: str, version: int | None = None
    ) -> list[tuple[ReportClaim, list[uuid.UUID]]]:
        """Claims for a report version, each with the sources supporting it."""
        report = await self.report(task_id=task_id, session_id=session_id, version=version)
        claims = list(
            (
                await self._session.scalars(
                    select(ReportClaim)
                    .where(ReportClaim.report_id == report.id)
                    .order_by(ReportClaim.ordinal)
                )
            ).all()
        )
        links = (
            await self._session.execute(
                select(ClaimSource.claim_id, ClaimSource.source_id).where(
                    ClaimSource.claim_id.in_([c.id for c in claims] or [uuid.uuid4()])
                )
            )
        ).all()

        by_claim: dict[uuid.UUID, list[uuid.UUID]] = {}
        for claim_id, source_id in links:
            by_claim.setdefault(claim_id, []).append(source_id)
        return [(claim, by_claim.get(claim.id, [])) for claim in claims]

    async def export_markdown(self, *, task_id: uuid.UUID, session_id: str) -> tuple[str, int]:
        """Return the approved report's Markdown, or refuse.

        The approval gate is the product: an unapproved report is not
        exportable, and this is the single place that rule is enforced.
        """
        task = await self.get(task_id=task_id, session_id=session_id)
        if task.status is not TaskStatus.APPROVED:
            raise ApprovalRequiredError(
                f"This task is {task.status.value}. A report can only be exported after "
                "a human has approved it.",
                status=task.status.value,
            )
        report = await self.report(task_id=task_id, session_id=session_id)
        return report.content_markdown, report.version

    async def _revision_count(self, task_id: uuid.UUID) -> int:
        count = await self._session.scalar(
            select(func.count())
            .select_from(ApprovalEvent)
            .where(
                ApprovalEvent.task_id == task_id,
                ApprovalEvent.action == ApprovalAction.REQUEST_REVISION,
            )
        )
        return int(count or 0)

    async def purge_expired(self, *, retention_days: int) -> int:
        """Hard-delete tasks past the retention window.

        Cascades remove steps, sources, reports, claims, evidence links,
        approvals and ledger rows: leftover visitor research content is a
        privacy problem, not just untidy data. Wired to a schedule in M4.
        """
        cutoff = datetime.now(UTC) - timedelta(days=retention_days)
        rows = await self._session.scalars(
            select(ResearchTask).where(ResearchTask.created_at < cutoff)
        )
        count = 0
        for task in rows.all():
            await self._session.delete(task)
            count += 1
        if count:
            log.info("tasks_purged", count=count, retention_days=retention_days)
        return count
