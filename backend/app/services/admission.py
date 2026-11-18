"""Admission control for a shared, metered deployment.

Everything here answers one question, asked **before** a task row is written
or a job is queued: may this request start work, and may that work reach a
real provider?

Four controls, in the order they are checked:

1. **The deployment's daily task ceiling.** A count per UTC day, so a bad day
   costs a day rather than a month.
2. **The deployment's daily spend ceiling.** Read from the ledger's own
   estimate, so it bounds what the deployment has actually cost rather than
   what it planned to.
3. **The per-session task limit.** One visitor cannot occupy the whole
   allowance.
4. **The access code**, which gates *live* research specifically. Without it a
   task is forced onto the deterministic offline profile rather than refused:
   the public demo still works, it just cannot spend anything.

That last point is the design. An anonymous visitor to a public deployment
gets the full workflow against the stub adapter and the fixture corpus --
real orchestration, real approval, zero cost and no provider call. Reaching a
live model or live search requires the code.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.logging import get_logger
from app.db.models import DemoBudget, ResearchTask
from app.domain.enums import LLMProvider, SearchMode, TaskStatus
from app.domain.errors import (
    DemoBudgetExceededError,
    DemoTaskLimitError,
    SessionTaskLimitError,
)

log = get_logger(__name__)

#: Statuses that still count against a visitor's allowance. A failed or
#: cancelled task does not: it produced nothing, and holding it against the
#: visitor would turn our outage into their problem.
OCCUPYING_STATUSES = (
    TaskStatus.QUEUED,
    TaskStatus.RUNNING,
    TaskStatus.AWAITING_APPROVAL,
    TaskStatus.APPROVED,
)


@dataclass(frozen=True, slots=True)
class Admission:
    """What a task is permitted to do, decided before it exists."""

    #: Providers the task will actually use. Recorded on the task row, so the
    #: decision is auditable after the fact and cannot be changed by a later
    #: configuration edit.
    llm_provider: LLMProvider
    search_mode: SearchMode
    #: True when the request presented a valid access code *and* the
    #: deployment has live providers configured.
    live: bool
    reason: str


def _valid_code(settings: Settings, presented: str | None) -> bool:
    """Constant-time comparison, so a wrong code leaks nothing by timing."""
    configured = settings.demo_access_code
    if configured is None or not presented:
        return False
    return hmac.compare_digest(presented, configured.get_secret_value())


class AdmissionService:
    """Decides whether a request may start a task, and on which providers."""

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings

    async def admit(self, *, session_id: str, access_code: str | None = None) -> Admission:
        """Raise if the request may not start a task; otherwise say what it may use.

        Raises:
            DemoTaskLimitError: the deployment's daily task ceiling is reached.
            DemoBudgetExceededError: the deployment's daily spend ceiling is reached.
            SessionTaskLimitError: this visitor already holds too many tasks.
        """
        today = datetime.now(UTC).date()
        await self._check_daily_ceilings(today)
        await self._check_session_limit(session_id)
        return self._choose_providers(access_code)

    # -- the checks ------------------------------------------------------

    async def _check_daily_ceilings(self, today: date) -> None:
        budget = await self._session.get(DemoBudget, today)
        started = budget.tasks_started if budget else 0
        spent = float(budget.cost_usd_spent) if budget else 0.0

        if started >= self._settings.demo_daily_task_limit:
            log.warning("demo_daily_task_limit_reached", started=started, day=str(today))
            raise DemoTaskLimitError(
                "The demo has reached its daily task limit. Try again tomorrow.",
                limit=self._settings.demo_daily_task_limit,
                started=started,
            )

        if spent >= self._settings.demo_daily_budget_usd:
            log.warning("demo_daily_budget_reached", spent=spent, day=str(today))
            raise DemoBudgetExceededError(
                "The demo has spent its daily budget. Try again tomorrow.",
                limit_usd=self._settings.demo_daily_budget_usd,
            )

    async def _check_session_limit(self, session_id: str) -> None:
        held = int(
            await self._session.scalar(
                select(func.count())
                .select_from(ResearchTask)
                .where(
                    ResearchTask.session_id == session_id,
                    ResearchTask.deleted_at.is_(None),
                    ResearchTask.status.in_(OCCUPYING_STATUSES),
                )
            )
            or 0
        )
        if held >= self._settings.demo_max_tasks_per_session:
            log.info("session_task_limit_reached", held=held)
            raise SessionTaskLimitError(
                f"This session already has {held} tasks. "
                "Finish or abandon one before starting another.",
                limit=self._settings.demo_max_tasks_per_session,
                held=held,
            )

    def _choose_providers(self, access_code: str | None) -> Admission:
        """Live providers only with a valid code; otherwise the offline profile."""
        settings = self._settings

        if settings.is_offline:
            return Admission(
                llm_provider=settings.llm_provider,
                search_mode=settings.search_provider,
                live=False,
                reason="deployment runs the offline profile",
            )

        if _valid_code(settings, access_code):
            return Admission(
                llm_provider=settings.llm_provider,
                search_mode=settings.search_provider,
                live=True,
                reason="valid access code",
            )

        # Configured for live providers, but this request did not present the
        # code. Downgrade rather than refuse: the demo still works.
        if access_code:
            log.warning("access_code_rejected")
        return Admission(
            llm_provider=LLMProvider.STUB,
            search_mode=SearchMode.FIXTURE,
            live=False,
            reason="no valid access code; forced onto the offline profile",
        )

    # -- accounting ------------------------------------------------------

    async def record_task_started(self) -> None:
        """Count a started task against the day, atomically.

        An upsert rather than read-modify-write: two concurrent submissions
        must not both read zero and both write one.
        """
        today = datetime.now(UTC).date()
        statement = (
            insert(DemoBudget)
            .values(day=today, tasks_started=1, cost_usd_spent=0)
            .on_conflict_do_update(
                index_elements=[DemoBudget.day],
                set_={"tasks_started": DemoBudget.tasks_started + 1},
            )
        )
        await self._session.execute(statement)

    async def record_spend(self, cost_usd: float) -> None:
        """Add a task's cost to the day's total."""
        if cost_usd <= 0:
            return
        today = datetime.now(UTC).date()
        statement = (
            insert(DemoBudget)
            .values(day=today, tasks_started=0, cost_usd_spent=cost_usd)
            .on_conflict_do_update(
                index_elements=[DemoBudget.day],
                set_={"cost_usd_spent": DemoBudget.cost_usd_spent + cost_usd},
            )
        )
        await self._session.execute(statement)
