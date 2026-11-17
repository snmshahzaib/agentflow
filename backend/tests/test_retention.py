"""Data retention.

A public demo collects strangers' research questions, the pages retrieved for
them and the reports written from them. The retention sweep is what stops that
accumulating indefinitely, so it is tested on real rows with real cascades
rather than mocked.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    ClaimSource,
    Report,
    ReportClaim,
    ResearchTask,
    Source,
    WorkflowStep,
)
from app.domain.enums import (
    LLMProvider,
    ReportStatus,
    SearchMode,
    StepStatus,
    SupportStatus,
    TaskStatus,
)
from app.domain.nodes import NodeName
from app.services.task_service import purge_tasks_older_than

pytestmark = pytest.mark.integration


async def _task_with_content(session: AsyncSession, *, age_days: float) -> uuid.UUID:
    """A task old enough to matter, with the content retention is about."""
    task = ResearchTask(
        session_id=f"sess-{uuid.uuid4().hex[:8]}",
        question="A visitor's private research question.",
        status=TaskStatus.APPROVED,
        search_mode=SearchMode.FIXTURE,
        llm_provider=LLMProvider.STUB,
        max_tokens_budget=100_000,
        max_calls_budget=20,
        created_at=datetime.now(UTC) - timedelta(days=age_days),
    )
    session.add(task)
    await session.flush()

    session.add(
        WorkflowStep(
            task_id=task.id,
            node=NodeName.RESEARCH,
            status=StepStatus.SUCCEEDED,
            attempt=1,
        )
    )
    source = Source(
        task_id=task.id,
        url="https://example.test/page",
        url_sha256="f" * 64,
        title="A retrieved page",
        excerpt="Text retrieved on the visitor's behalf.",
        rank=0,
        subtask_index=0,
        provider=SearchMode.FIXTURE,
        is_fixture=True,
    )
    report = Report(
        task_id=task.id,
        version=1,
        status=ReportStatus.APPROVED,
        content_markdown="# The visitor's report",
    )
    session.add_all([source, report])
    await session.flush()

    claim = ReportClaim(
        report_id=report.id,
        ordinal=0,
        claim_text="A claim about the visitor's question.",
        support_status=SupportStatus.SUPPORTED,
    )
    session.add(claim)
    await session.flush()
    session.add(ClaimSource(claim_id=claim.id, source_id=source.id))
    await session.flush()
    return task.id


async def _count(session: AsyncSession, model: type, column: object, value: object) -> int:
    return int(
        await session.scalar(select(func.count()).select_from(model).where(column == value)) or 0
    )


class TestRetentionSweep:
    async def test_a_task_past_the_window_is_deleted_with_all_its_content(
        self, db_session: AsyncSession
    ) -> None:
        task_id = await _task_with_content(db_session, age_days=30)

        removed = await purge_tasks_older_than(db_session, retention_days=7)
        await db_session.flush()

        assert removed >= 1
        assert await db_session.get(ResearchTask, task_id) is None
        # The cascades are the point: a deleted task that leaves its retrieved
        # excerpts and report behind has not been deleted in any sense that
        # matters for privacy.
        assert await _count(db_session, Source, Source.task_id, task_id) == 0
        assert await _count(db_session, WorkflowStep, WorkflowStep.task_id, task_id) == 0
        assert await _count(db_session, Report, Report.task_id, task_id) == 0

    async def test_a_task_inside_the_window_is_untouched(self, db_session: AsyncSession) -> None:
        recent = await _task_with_content(db_session, age_days=1)

        await purge_tasks_older_than(db_session, retention_days=7)
        await db_session.flush()

        assert await db_session.get(ResearchTask, recent) is not None
        assert await _count(db_session, Source, Source.task_id, recent) == 1

    async def test_the_boundary_is_the_retention_window(self, db_session: AsyncSession) -> None:
        just_inside = await _task_with_content(db_session, age_days=6.9)
        just_outside = await _task_with_content(db_session, age_days=7.1)

        await purge_tasks_older_than(db_session, retention_days=7)
        await db_session.flush()

        assert await db_session.get(ResearchTask, just_inside) is not None
        assert await db_session.get(ResearchTask, just_outside) is None

    async def test_a_sweep_with_nothing_to_do_is_harmless(self, db_session: AsyncSession) -> None:
        recent = await _task_with_content(db_session, age_days=0)
        before = await _count(db_session, ResearchTask, ResearchTask.id, recent)

        removed = await purge_tasks_older_than(db_session, retention_days=3650)

        assert removed == 0
        assert await _count(db_session, ResearchTask, ResearchTask.id, recent) == before


class TestScheduling:
    """The sweep is only a control if something actually runs it."""

    def test_the_retention_job_is_registered_and_scheduled(self) -> None:
        from app.workers.jobs import purge_expired_tasks
        from app.workers.settings import WorkerSettings

        assert purge_expired_tasks in WorkerSettings.functions
        scheduled = [
            job
            for job in WorkerSettings.cron_jobs
            if getattr(job, "coroutine", None) is purge_expired_tasks
        ]
        assert scheduled, "the retention sweep must be on a schedule, not just defined"

    def test_the_sweep_does_not_run_at_startup(self) -> None:
        """A restart loop must not purge repeatedly."""
        from app.workers.jobs import purge_expired_tasks
        from app.workers.settings import WorkerSettings

        job = next(
            j
            for j in WorkerSettings.cron_jobs
            if getattr(j, "coroutine", None) is purge_expired_tasks
        )
        assert job.run_at_startup is False
