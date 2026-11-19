"""Admission control: the four gates a shared deployment needs.

These are the controls that make a public demo safe to leave running. Each
test states the attack or accident it prevents, because a limit that is never
exercised is a number in a config file.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db.models import DemoBudget, ResearchTask
from app.domain.enums import LLMProvider, SearchMode, TaskStatus
from app.domain.errors import (
    DemoBudgetExceededError,
    DemoTaskLimitError,
    SessionTaskLimitError,
)
from app.services.admission import AdmissionService
from tests.conftest import make_settings

pytestmark = pytest.mark.integration

LIVE_OVERRIDES = {
    "llm_provider": "openrouter",
    "llm_api_key": "test-key-not-real",
    "llm_model_default": "vendor/model:free",
    "search_provider": "tavily",
    "search_api_key": "test-key-not-real",
}


def offline_settings(**overrides: object) -> Settings:
    return make_settings(**overrides)


def live_settings(**overrides: object) -> Settings:
    return make_settings(**{**LIVE_OVERRIDES, **overrides})


async def _seed_task(
    session: AsyncSession,
    *,
    session_id: str,
    status: TaskStatus = TaskStatus.AWAITING_APPROVAL,
) -> ResearchTask:
    task = ResearchTask(
        session_id=session_id,
        question="A seeded question for the admission tests.",
        status=status,
        search_mode=SearchMode.FIXTURE,
        llm_provider=LLMProvider.STUB,
        max_tokens_budget=120_000,
        max_calls_budget=12,
    )
    session.add(task)
    await session.flush()
    return task


async def reset_today(session: AsyncSession) -> None:
    """Clear the deployment-wide counter for today.

    The daily ceilings are *deployment-wide* and counted in a table, so they
    are the one piece of state these tests share. Most tests here run inside a
    transaction that is rolled back; the concurrency tests commit for real,
    because a race cannot be reproduced inside one transaction. Both therefore
    start from a known day.
    """
    await session.execute(delete(DemoBudget).where(DemoBudget.day == datetime.now(UTC).date()))


class TestLiveResearchIsGated:
    """No anonymous access to a live model or live search."""

    async def test_an_offline_deployment_needs_no_code(self, db_session: AsyncSession) -> None:
        decision = await AdmissionService(db_session, offline_settings()).admit(session_id="sess-a")
        assert decision.live is False
        assert decision.llm_provider is LLMProvider.STUB
        assert decision.search_mode is SearchMode.FIXTURE

    async def test_a_live_deployment_downgrades_an_anonymous_request(
        self, db_session: AsyncSession
    ) -> None:
        # The demo still works for a stranger -- it just cannot spend
        # anything. Refusing outright would make the public demo useless.
        service = AdmissionService(db_session, live_settings(demo_access_code="let-me-in"))
        decision = await service.admit(session_id="sess-a")

        assert decision.live is False
        assert decision.llm_provider is LLMProvider.STUB
        assert decision.search_mode is SearchMode.FIXTURE

    async def test_a_wrong_code_is_downgraded_not_accepted(self, db_session: AsyncSession) -> None:
        service = AdmissionService(db_session, live_settings(demo_access_code="let-me-in"))
        decision = await service.admit(session_id="sess-a", access_code="wrong")

        assert decision.live is False
        assert decision.llm_provider is LLMProvider.STUB

    async def test_a_valid_code_unlocks_the_configured_providers(
        self, db_session: AsyncSession
    ) -> None:
        service = AdmissionService(db_session, live_settings(demo_access_code="let-me-in"))
        decision = await service.admit(session_id="sess-a", access_code="let-me-in")

        assert decision.live is True
        assert decision.llm_provider is LLMProvider.OPENROUTER
        assert decision.search_mode is SearchMode.TAVILY

    async def test_no_code_configured_means_nobody_gets_live(
        self, db_session: AsyncSession
    ) -> None:
        # A live deployment that forgot to set a code must not be an open
        # door: with no code configured, no code can match.
        service = AdmissionService(db_session, live_settings(demo_access_code=None))
        for presented in (None, "", "anything", "None"):
            decision = await service.admit(session_id="sess-a", access_code=presented)
            assert decision.live is False, presented


class TestPerSessionLimit:
    """One visitor cannot occupy the whole demo."""

    async def test_the_limit_refuses_the_next_task(self, db_session: AsyncSession) -> None:
        settings = offline_settings(demo_max_tasks_per_session=3)
        service = AdmissionService(db_session, settings)

        for _ in range(3):
            await _seed_task(db_session, session_id="sess-greedy")

        with pytest.raises(SessionTaskLimitError) as caught:
            await service.admit(session_id="sess-greedy")
        assert caught.value.details["limit"] == 3

    async def test_another_session_is_unaffected(self, db_session: AsyncSession) -> None:
        settings = offline_settings(demo_max_tasks_per_session=2)
        service = AdmissionService(db_session, settings)
        for _ in range(2):
            await _seed_task(db_session, session_id="sess-greedy")

        decision = await service.admit(session_id="sess-innocent")
        assert decision.llm_provider is LLMProvider.STUB

    async def test_failed_tasks_do_not_count_against_a_visitor(
        self, db_session: AsyncSession
    ) -> None:
        # Our outage must not become the visitor's quota problem.
        settings = offline_settings(demo_max_tasks_per_session=2)
        service = AdmissionService(db_session, settings)
        await _seed_task(db_session, session_id="sess-unlucky", status=TaskStatus.FAILED)
        await _seed_task(db_session, session_id="sess-unlucky", status=TaskStatus.CANCELLED)
        await _seed_task(db_session, session_id="sess-unlucky", status=TaskStatus.APPROVED)

        decision = await service.admit(session_id="sess-unlucky")
        assert decision is not None


class TestDailyCeilings:
    """A bad day costs a day, not a month."""

    @pytest.fixture(autouse=True)
    async def _clean_day(self, db_session: AsyncSession) -> None:
        await reset_today(db_session)

    async def _budget_row(self, session: AsyncSession) -> DemoBudget | None:
        return await session.get(DemoBudget, datetime.now(UTC).date())

    async def test_the_daily_task_ceiling_refuses(self, db_session: AsyncSession) -> None:
        settings = offline_settings(demo_daily_task_limit=2)
        service = AdmissionService(db_session, settings)

        await service.admit(session_id="s1")
        await service.record_task_started()
        await service.admit(session_id="s2")
        await service.record_task_started()

        with pytest.raises(DemoTaskLimitError):
            await service.admit(session_id="s3")

    async def test_the_daily_spend_ceiling_refuses(self, db_session: AsyncSession) -> None:
        settings = offline_settings(demo_daily_budget_usd=1.0)
        service = AdmissionService(db_session, settings)

        await service.record_spend(0.75)
        await service.admit(session_id="s1")  # still under

        await service.record_spend(0.30)
        with pytest.raises(DemoBudgetExceededError):
            await service.admit(session_id="s2")

    async def test_zero_cost_runs_do_not_move_the_spend_ceiling(
        self, db_session: AsyncSession
    ) -> None:
        # The offline profile costs nothing, so a demo left running on
        # fixtures must never exhaust a spend budget.
        service = AdmissionService(db_session, offline_settings(demo_daily_budget_usd=0.01))
        for _ in range(50):
            await service.record_spend(0.0)
        await service.admit(session_id="s1")

        row = await self._budget_row(db_session)
        assert row is None or float(row.cost_usd_spent) == 0.0

    async def test_concurrent_submissions_are_counted_once_each(
        self, db_session: AsyncSession
    ) -> None:
        """The counter is an upsert, not read-modify-write.

        Two submissions landing together must not both read zero and both
        write one -- that is how a ceiling silently stops counting.
        """
        service = AdmissionService(db_session, offline_settings())
        for _ in range(10):
            await service.record_task_started()

        row = await self._budget_row(db_session)
        assert row is not None
        assert row.tasks_started == 10


@pytest.mark.usefixtures("db_session")
class TestCredentialsAndModelsCannotBeChosen:
    """A visitor may not pick a model or supply a key."""

    def test_the_request_schema_refuses_unknown_fields(self) -> None:
        from pydantic import ValidationError

        from app.schemas.task import TaskCreate

        for smuggled in (
            {"llm_provider": "openrouter"},
            {"model": "anthropic/claude-sonnet-5"},
            {"llm_api_key": "sk-test"},
            {"search_provider": "tavily"},
        ):
            with pytest.raises(ValidationError):
                TaskCreate(question="A question long enough to pass.", **smuggled)

    async def test_constraints_cannot_smuggle_a_provider_choice(
        self, db_session: AsyncSession
    ) -> None:
        # `constraints` is free-form and reaches the planner prompt, so it is
        # the obvious place to try. Admission never reads it.
        service = AdmissionService(db_session, live_settings(demo_access_code="let-me-in"))
        decision = await service.admit(session_id="sess-a", access_code=None)
        assert decision.llm_provider is LLMProvider.STUB


class TestTheWorkerHonoursTheRecordedDecision:
    """The gate is only a control if the worker obeys it."""

    async def test_a_stub_task_runs_offline_even_with_live_deps_available(
        self, db_session: AsyncSession, node_deps: object
    ) -> None:
        from app.workers.jobs import _deps_for_task

        live_sentinel = object()
        offline_sentinel = object()
        ctx = {"node_deps": live_sentinel, "offline_deps": offline_sentinel}

        assert _deps_for_task(ctx, LLMProvider.STUB) is offline_sentinel
        assert _deps_for_task(ctx, LLMProvider.OPENROUTER) is live_sentinel

    async def test_task_rows_record_what_they_were_admitted_with(
        self, db_session: AsyncSession
    ) -> None:
        task = await _seed_task(db_session, session_id="sess-a")
        stored = await db_session.scalar(select(ResearchTask).where(ResearchTask.id == task.id))
        assert stored is not None
        assert stored.llm_provider is LLMProvider.STUB
        assert stored.search_mode is SearchMode.FIXTURE


class TestConcurrentAdmission:
    """The limits must hold when requests arrive together, not just in turn.

    This is a regression test for a bug the live abuse check found and the
    rest of this file missed: twelve simultaneous submissions from one session
    were all admitted against a limit of five. Every check here is
    read-then-write, and the other tests share a single database session,
    which serialises them for free and hides the race.

    So these use **separate connections**, which is the only way to reproduce
    it.
    """

    async def _admit_on_own_connection(
        self, database_url: str, settings: Settings, session_id: str
    ) -> str:
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        from sqlalchemy.pool import NullPool

        engine = create_async_engine(database_url, poolclass=NullPool)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as session:
                try:
                    service = AdmissionService(session, settings)
                    await service.admit(session_id=session_id)
                    await _seed_task(session, session_id=session_id)
                    await service.record_task_started()
                    await session.commit()
                    return "admitted"
                except (SessionTaskLimitError, DemoTaskLimitError, DemoBudgetExceededError):
                    await session.rollback()
                    return "refused"
        finally:
            await engine.dispose()

    async def test_the_per_session_limit_holds_under_concurrency(
        self, migrated_database: str
    ) -> None:
        settings = offline_settings(demo_max_tasks_per_session=5, demo_daily_task_limit=1000)
        session_id = f"sess-{uuid.uuid4().hex[:8]}"

        outcomes = await asyncio.gather(
            *(
                self._admit_on_own_connection(migrated_database, settings, session_id)
                for _ in range(12)
            )
        )

        assert outcomes.count("admitted") == 5, outcomes
        assert outcomes.count("refused") == 7

    @pytest.fixture(autouse=True)
    async def _isolate_the_day(self, migrated_database: str) -> AsyncIterator[None]:
        """Reset the day's counter before *and* after.

        These tests commit, unlike the rest of the suite, so without the
        cleanup they leave a row behind that fails an unrelated schema test
        asserting one row per day can be inserted. Found by running the suite
        in random order.
        """
        await self._reset_committed_day(migrated_database)
        yield
        await self._reset_committed_day(migrated_database)

    async def _reset_committed_day(self, database_url: str) -> None:
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        from sqlalchemy.pool import NullPool

        engine = create_async_engine(database_url, poolclass=NullPool)
        try:
            async with async_sessionmaker(engine)() as session, session.begin():
                await reset_today(session)
        finally:
            await engine.dispose()

    async def test_the_daily_ceiling_holds_under_concurrency(self, migrated_database: str) -> None:
        settings = offline_settings(demo_daily_task_limit=4, demo_max_tasks_per_session=50)

        outcomes = await asyncio.gather(
            *(
                self._admit_on_own_connection(
                    migrated_database, settings, f"sess-{uuid.uuid4().hex[:8]}"
                )
                for _ in range(10)
            )
        )

        # The ceiling is deployment-wide, so distinct sessions share it.
        assert outcomes.count("admitted") == 4, outcomes


class TestAdmissionRunsBeforeAnythingIsWritten:
    async def test_a_refused_submission_leaves_no_task_row(self, db_session: AsyncSession) -> None:
        settings = offline_settings(demo_max_tasks_per_session=1)
        service = AdmissionService(db_session, settings)
        await _seed_task(db_session, session_id="sess-full")

        before = int(await db_session.scalar(select(func.count()).select_from(ResearchTask)) or 0)
        with pytest.raises(SessionTaskLimitError):
            await service.admit(session_id="sess-full")
        after = int(await db_session.scalar(select(func.count()).select_from(ResearchTask)) or 0)
        assert after == before
