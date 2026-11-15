"""Database invariants.

These tests assert the constraints the design depends on, at the level where
they are actually enforced. Several of them are the database half of the
plan's acceptance criteria: a retried worker job must not be able to duplicate
a step or a report, and a source must not be stored twice for one task.

Marked ``integration``: they need PostgreSQL. Run the fast subset with
``pytest -m "not integration"``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    ApprovalEvent,
    ClaimSource,
    DemoBudget,
    LLMCall,
    Report,
    ReportClaim,
    ResearchTask,
    Source,
    WorkflowStep,
)
from app.domain.enums import (
    ApprovalAction,
    AttemptOutcome,
    LLMProvider,
    OutputMode,
    ReportStatus,
    SearchMode,
    StepStatus,
    SupportStatus,
    TaskStatus,
)
from app.domain.nodes import NodeName

pytestmark = pytest.mark.integration


async def _task(session: AsyncSession, **overrides: object) -> ResearchTask:
    task = ResearchTask(
        session_id=overrides.pop("session_id", "sess-" + uuid.uuid4().hex[:8]),  # type: ignore[arg-type]
        question=overrides.pop("question", "Compare three workflow orchestration frameworks."),  # type: ignore[arg-type]
        search_mode=SearchMode.FIXTURE,
        llm_provider=LLMProvider.STUB,
        max_tokens_budget=120_000,
        max_calls_budget=12,
        **overrides,
    )
    session.add(task)
    await session.flush()
    return task


class TestTaskDefaults:
    async def test_a_new_task_is_queued_with_a_server_generated_id(
        self, db_session: AsyncSession
    ) -> None:
        task = await _task(db_session)
        assert isinstance(task.id, uuid.UUID)
        assert task.status is TaskStatus.QUEUED
        assert task.current_node is None
        assert task.tokens_used == 0
        assert task.calls_used == 0
        assert task.created_at is not None

    async def test_provenance_is_recorded_on_every_task(self, db_session: AsyncSession) -> None:
        """What produced a task's content must be queryable, not inferred."""
        task = await _task(db_session)
        assert task.search_mode is SearchMode.FIXTURE
        assert task.llm_provider is LLMProvider.STUB

    @pytest.mark.parametrize("question", ["too short", "x" * 2001])
    async def test_question_length_is_enforced_by_the_database(
        self, db_session: AsyncSession, question: str
    ) -> None:
        with pytest.raises(IntegrityError):
            await _task(db_session, question=question)

    async def test_an_invalid_status_is_rejected_by_the_database_itself(
        self, db_session: AsyncSession
    ) -> None:
        """Enums are stored as VARCHAR + CHECK, so the CHECK must exist.

        Deliberately raw SQL: going through the ORM only proves SQLAlchemy's
        client-side enum validation, which would pass even if the database
        had no constraint at all.
        """
        task = await _task(db_session)
        with pytest.raises((IntegrityError, DBAPIError)):
            await db_session.execute(
                text("UPDATE research_tasks SET status = 'not_a_real_status' WHERE id = :id"),
                {"id": task.id},
            )
            await db_session.flush()

    async def test_the_status_check_constraint_is_present_in_the_catalog(
        self, db_session: AsyncSession
    ) -> None:
        present = await db_session.scalar(
            text(
                "SELECT count(*) FROM pg_constraint "
                "WHERE conname = 'ck_research_tasks_task_status' AND contype = 'c'"
            )
        )
        assert present == 1


class TestRetryIdempotency:
    """A duplicated worker job must not be able to duplicate history."""

    async def test_the_same_node_attempt_cannot_be_recorded_twice(
        self, db_session: AsyncSession
    ) -> None:
        task = await _task(db_session)
        for _ in range(2):
            db_session.add(
                WorkflowStep(
                    task_id=task.id,
                    node=NodeName.PLAN,
                    status=StepStatus.SUCCEEDED,
                    attempt=1,
                )
            )
        with pytest.raises(IntegrityError):
            await db_session.flush()

    async def test_a_genuine_retry_is_a_new_attempt_number(self, db_session: AsyncSession) -> None:
        task = await _task(db_session)
        db_session.add_all(
            [
                WorkflowStep(
                    task_id=task.id, node=NodeName.PLAN, status=StepStatus.FAILED, attempt=1
                ),
                WorkflowStep(
                    task_id=task.id, node=NodeName.PLAN, status=StepStatus.SUCCEEDED, attempt=2
                ),
            ]
        )
        await db_session.flush()
        count = await db_session.scalar(
            select(func.count()).select_from(WorkflowStep).where(WorkflowStep.task_id == task.id)
        )
        assert count == 2

    async def test_a_report_version_cannot_be_written_twice(self, db_session: AsyncSession) -> None:
        task = await _task(db_session)
        for _ in range(2):
            db_session.add(Report(task_id=task.id, version=1, content_markdown="# Report"))
        with pytest.raises(IntegrityError):
            await db_session.flush()

    async def test_a_revision_creates_a_new_version(self, db_session: AsyncSession) -> None:
        task = await _task(db_session)
        v1 = Report(
            task_id=task.id,
            version=1,
            content_markdown="# v1",
            status=ReportStatus.SUPERSEDED,
        )
        db_session.add(v1)
        await db_session.flush()
        v2 = Report(task_id=task.id, version=2, content_markdown="# v2")
        db_session.add(v2)
        await db_session.flush()
        v1.superseded_by = v2.id
        await db_session.flush()
        assert v1.superseded_by == v2.id


class TestSourceDeduplication:
    async def test_the_same_url_cannot_be_stored_twice_for_one_task(
        self, db_session: AsyncSession
    ) -> None:
        task = await _task(db_session)
        digest = "a" * 64
        for i in range(2):
            db_session.add(
                Source(
                    task_id=task.id,
                    subtask_index=i,
                    url="https://example.test/a",
                    url_sha256=digest,
                    provider="fixture",
                    rank=1,
                    is_fixture=True,
                )
            )
        with pytest.raises(IntegrityError):
            await db_session.flush()

    async def test_the_same_url_may_appear_under_different_tasks(
        self, db_session: AsyncSession
    ) -> None:
        digest = "b" * 64
        for _ in range(2):
            task = await _task(db_session)
            db_session.add(
                Source(
                    task_id=task.id,
                    subtask_index=0,
                    url="https://example.test/shared",
                    url_sha256=digest,
                    provider="fixture",
                    rank=1,
                    is_fixture=True,
                )
            )
        await db_session.flush()


class TestClaimSupportIsQueryable:
    """Unsupported claims must be flagged in data, not buried in markdown."""

    async def test_unsupported_claims_can_be_counted_in_sql(self, db_session: AsyncSession) -> None:
        task = await _task(db_session)
        report = Report(task_id=task.id, version=1, content_markdown="# r")
        db_session.add(report)
        await db_session.flush()

        db_session.add_all(
            [
                ReportClaim(
                    report_id=report.id,
                    ordinal=0,
                    claim_text="Backed by a retrieved source.",
                    support_status=SupportStatus.SUPPORTED,
                ),
                ReportClaim(
                    report_id=report.id,
                    ordinal=1,
                    claim_text="Not backed by anything retrieved.",
                    support_status=SupportStatus.UNSUPPORTED,
                ),
                ReportClaim(
                    report_id=report.id,
                    ordinal=2,
                    claim_text="Partially backed.",
                    support_status=SupportStatus.PARTIAL,
                ),
            ]
        )
        await db_session.flush()

        unsupported = await db_session.scalar(
            select(func.count())
            .select_from(ReportClaim)
            .where(
                ReportClaim.report_id == report.id,
                ReportClaim.support_status == SupportStatus.UNSUPPORTED,
            )
        )
        assert unsupported == 1

    async def test_a_claim_links_to_the_sources_that_support_it(
        self, db_session: AsyncSession
    ) -> None:
        task = await _task(db_session)
        report = Report(task_id=task.id, version=1, content_markdown="# r")
        source = Source(
            task_id=task.id,
            subtask_index=0,
            url="https://example.test/evidence",
            url_sha256="c" * 64,
            provider="fixture",
            rank=1,
            is_fixture=True,
        )
        db_session.add_all([report, source])
        await db_session.flush()

        claim = ReportClaim(
            report_id=report.id,
            ordinal=0,
            claim_text="A claim with evidence.",
            support_status=SupportStatus.SUPPORTED,
        )
        db_session.add(claim)
        await db_session.flush()
        db_session.add(ClaimSource(claim_id=claim.id, source_id=source.id))
        await db_session.flush()

        linked = await db_session.scalar(
            select(func.count()).select_from(ClaimSource).where(ClaimSource.claim_id == claim.id)
        )
        assert linked == 1

    async def test_claim_ordinals_are_unique_within_a_report(
        self, db_session: AsyncSession
    ) -> None:
        task = await _task(db_session)
        report = Report(task_id=task.id, version=1, content_markdown="# r")
        db_session.add(report)
        await db_session.flush()
        for _ in range(2):
            db_session.add(
                ReportClaim(
                    report_id=report.id,
                    ordinal=0,
                    claim_text="Duplicate ordinal.",
                    support_status=SupportStatus.SUPPORTED,
                )
            )
        with pytest.raises(IntegrityError):
            await db_session.flush()


class TestCostLedger:
    async def test_a_call_records_requested_and_served_models_separately(
        self, db_session: AsyncSession
    ) -> None:
        """When the fallback chain fires these differ, and the audit trail
        must show which model actually produced the output."""
        task = await _task(db_session)
        db_session.add(
            LLMCall(
                task_id=task.id,
                node=NodeName.DRAFT,
                provider=LLMProvider.STUB,
                model_requested="vendor-a/primary",
                model_served="vendor-b/fallback",
                output_mode=OutputMode.JSON_SCHEMA,
                tokens_in=1200,
                tokens_out=800,
                latency_ms=4200,
                cost_usd_estimate=0.0124,
                attempt_outcome=AttemptOutcome.FELL_BACK,
            )
        )
        await db_session.flush()
        call = await db_session.scalar(select(LLMCall).where(LLMCall.task_id == task.id))
        assert call is not None
        assert call.model_requested != call.model_served
        assert call.attempt_outcome is AttemptOutcome.FELL_BACK

    async def test_negative_token_counts_are_rejected(self, db_session: AsyncSession) -> None:
        task = await _task(db_session)
        db_session.add(
            LLMCall(
                task_id=task.id,
                node=NodeName.PLAN,
                provider=LLMProvider.STUB,
                model_requested="vendor-a/primary",
                output_mode=OutputMode.JSON_SCHEMA,
                tokens_in=-1,
                attempt_outcome=AttemptOutcome.OK,
            )
        )
        with pytest.raises(IntegrityError):
            await db_session.flush()


class TestCascades:
    async def test_deleting_a_task_removes_its_entire_history(
        self, db_session: AsyncSession
    ) -> None:
        """The retention job depends on this; orphaned research content is a
        privacy problem, not just untidy."""
        task = await _task(db_session)
        report = Report(task_id=task.id, version=1, content_markdown="# r")
        db_session.add_all(
            [
                report,
                WorkflowStep(
                    task_id=task.id, node=NodeName.PLAN, status=StepStatus.SUCCEEDED, attempt=1
                ),
                Source(
                    task_id=task.id,
                    subtask_index=0,
                    url="https://example.test/x",
                    url_sha256="d" * 64,
                    provider="fixture",
                    rank=1,
                ),
                ApprovalEvent(
                    task_id=task.id,
                    report_version=1,
                    session_id=task.session_id,
                    action=ApprovalAction.APPROVE,
                ),
            ]
        )
        await db_session.flush()
        db_session.add(
            ReportClaim(
                report_id=report.id,
                ordinal=0,
                claim_text="A claim that should disappear with the task.",
                support_status=SupportStatus.SUPPORTED,
            )
        )
        await db_session.flush()

        await db_session.delete(task)
        await db_session.flush()

        for model, column in (
            (WorkflowStep, WorkflowStep.task_id),
            (Source, Source.task_id),
            (Report, Report.task_id),
            (ApprovalEvent, ApprovalEvent.task_id),
        ):
            remaining = await db_session.scalar(
                select(func.count()).select_from(model).where(column == task.id)
            )
            assert remaining == 0, f"{model.__tablename__} rows survived the cascade"

        orphan_claims = await db_session.scalar(
            select(func.count()).select_from(ReportClaim).where(ReportClaim.report_id == report.id)
        )
        assert orphan_claims == 0


class TestDemoBudget:
    async def test_one_row_per_day_with_a_non_negative_spend(
        self, db_session: AsyncSession
    ) -> None:
        day = datetime.now(UTC).date()
        db_session.add(DemoBudget(day=day, cost_usd_spent=0.42, tasks_started=3))
        await db_session.flush()
        db_session.add(DemoBudget(day=day, cost_usd_spent=0.1, tasks_started=1))
        with pytest.raises(IntegrityError):
            await db_session.flush()
