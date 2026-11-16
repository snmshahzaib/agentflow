"""Shared test fixtures.

Tests must never read a developer's real ``.env``: ``make_settings`` builds a
``Settings`` instance with dotenv loading disabled, so every test states its
own configuration explicitly.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator
from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import Settings, get_settings

if TYPE_CHECKING:
    from app.graph.deps import NodeDeps

#: Captured at import time, before the environment-isolation fixture runs.
TEST_DATABASE_URL = (
    os.environ.get("TEST_DATABASE_URL")
    or os.environ.get("DATABASE_URL")
    or "postgresql+asyncpg://agentflow:agentflow@localhost:5432/agentflow_test"
)

#: Minimal configuration for an offline, key-free system.
OFFLINE_ENV: dict[str, Any] = {
    "app_env": "test",
    "llm_provider": "stub",
    "search_provider": "fixture",
    "log_format": "json",
}


def make_settings(**overrides: Any) -> Settings:
    """Build settings from explicit values only, ignoring any .env on disk."""
    return Settings(_env_file=None, **{**OFFLINE_ENV, **overrides})


@pytest.fixture
def settings() -> Settings:
    return make_settings()


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Iterator[None]:
    """Keep the cached singleton from leaking between tests."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _isolate_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove every AgentFlow setting from the ambient environment.

    ``_env_file=None`` only disables dotenv loading; pydantic-settings still
    reads real environment variables. CI exports DATABASE_URL and friends for
    the integration tests, which silently overrode values a unit test had
    passed explicitly. Stripping the whole field set makes each test state its
    own configuration and nothing else.

    Integration tests that need the real database ask for it through a
    fixture, rather than relying on an ambient variable.
    """
    for field in Settings.model_fields:
        monkeypatch.delenv(field.upper(), raising=False)
        monkeypatch.delenv(field.lower(), raising=False)


# ---------------------------------------------------------------------------
# Integration fixtures (require PostgreSQL; marked `integration`)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def migrated_database() -> str:
    """Apply migrations to the test database once per session.

    Deliberately runs the real Alembic migrations rather than
    ``metadata.create_all``: the migration is what production will execute, so
    it is what the tests must exercise.
    """
    from alembic import command
    from alembic.config import Config

    cfg = Config("alembic.ini")
    cfg.set_main_option(
        "sqlalchemy.url",
        TEST_DATABASE_URL.replace("postgresql+asyncpg://", "postgresql+psycopg://", 1),
    )
    command.upgrade(cfg, "head")
    return TEST_DATABASE_URL


@pytest.fixture
async def db_session(migrated_database: str) -> AsyncIterator[AsyncSession]:
    """A session whose transaction is rolled back, so tests cannot leak rows."""
    engine = create_async_engine(migrated_database, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            session = AsyncSession(
                bind=connection,
                expire_on_commit=False,
                join_transaction_mode="create_savepoint",
            )
            try:
                yield session
            finally:
                await session.close()
                await transaction.rollback()
    finally:
        await engine.dispose()


@pytest.fixture
async def node_deps(migrated_database: str) -> AsyncIterator[NodeDeps]:
    """Real database, deterministic stub model, committed fixture corpus.

    This is the configuration the offline demo and CI both run, so tests
    exercise exactly the path a reader of the repository would.
    """
    from app.adapters.llm.stub import StubLLMClient
    from app.adapters.search.fixture import FixtureSearchProvider
    from app.graph.deps import NodeDeps

    engine = create_async_engine(migrated_database, poolclass=NullPool)
    try:
        yield NodeDeps(
            settings=make_settings(),
            session_factory=async_sessionmaker(engine, expire_on_commit=False),
            llm=StubLLMClient(),
            search=FixtureSearchProvider(),
        )
    finally:
        await engine.dispose()


async def create_task_row(
    deps: NodeDeps,
    *,
    question: str = "Compare three open-source workflow orchestration frameworks.",
    session_id: str | None = None,
) -> str:
    """Insert a queued task and return its id as a string."""
    import uuid as _uuid

    from app.db.models import ResearchTask

    async with deps.session_factory() as session, session.begin():
        task = ResearchTask(
            session_id=session_id or f"sess-{_uuid.uuid4().hex[:8]}",
            question=question,
            # Taken from the resolved configuration, exactly as TaskService
            # does. Hardcoding STUB here made a live run's report correctly
            # describe itself as stub-produced.
            search_mode=deps.settings.search_provider,
            llm_provider=deps.settings.llm_provider,
            max_tokens_budget=deps.settings.llm_max_tokens_per_task,
            max_calls_budget=deps.settings.llm_max_calls_per_task,
        )
        session.add(task)
        await session.flush()
        task.graph_thread_id = str(task.id)
        return str(task.id)


# ---------------------------------------------------------------------------
# Live provider fixtures (billable; skipped unless explicitly configured)
# ---------------------------------------------------------------------------

#: Ceiling on cumulative spend across every live test run, in USD. Read from
#: the llm_calls ledger before each live test, so a runaway loop stops rather
#: than quietly spending. Approved for M3: $5.
LIVE_TEST_BUDGET_USD = float(os.environ.get("M3_TEST_BUDGET_USD", "5.00"))

#: Captured at import, before the environment-isolation fixture strips it.
#: That fixture keeps offline tests hermetic; live tests legitimately need the
#: real credential, so it is read once here rather than at fixture time.
LIVE_API_KEY = os.environ.get("LLM_API_KEY", "").strip()
LIVE_BASE_URL = os.environ.get("LLM_BASE_URL", "https://openrouter.ai/api/v1")

LIVE_ENABLED = (
    os.environ.get("LLM_PROVIDER", "").lower() == "openrouter"
    and bool(LIVE_API_KEY)
    and not LIVE_API_KEY.startswith("__")
)

#: Cheap model used for the bulk of live assertions. Overridable so the suite
#: is not pinned to one vendor's naming.
LIVE_DEV_MODEL = os.environ.get("M3_DEV_MODEL", "anthropic/claude-haiku-5.5")
#: A model from a different vendor, for cross-provider assertions.
LIVE_ALT_MODEL = os.environ.get("M3_ALT_MODEL", "openai/gpt-6-luna")
#: When set, refuse any model that is not free. Used by the free development
#: profile so a billable call cannot happen during free-tier testing.
LIVE_FREE_ONLY = os.environ.get("M3_FREE_ONLY", "").lower() in {"1", "true", "yes"}


async def live_spend_so_far(database_url: str) -> float:
    """Total estimated spend recorded against a live provider."""
    from sqlalchemy import func, select

    from app.db.models import LLMCall
    from app.domain.enums import LLMProvider

    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        async with AsyncSession(engine) as session:
            total = await session.scalar(
                select(func.coalesce(func.sum(LLMCall.cost_usd_estimate), 0)).where(
                    LLMCall.provider == LLMProvider.OPENROUTER
                )
            )
        return float(total or 0.0)
    finally:
        await engine.dispose()


@pytest.fixture
async def live_budget_guard(migrated_database: str) -> float:
    """Refuse to start another live test once the approved cap is reached.

    This is the mechanism that makes the budget real rather than an intention:
    it reads what has actually been spent, not what was planned.
    """
    spent = await live_spend_so_far(migrated_database)
    if spent >= LIVE_TEST_BUDGET_USD:
        pytest.fail(
            f"Live test budget exhausted: ${spent:.4f} spent of "
            f"${LIVE_TEST_BUDGET_USD:.2f} approved. Raise M3_TEST_BUDGET_USD "
            "only with the repository owner's approval."
        )
    return spent


@pytest.fixture
async def live_deps(migrated_database: str, live_budget_guard: float) -> AsyncIterator[NodeDeps]:
    """Node dependencies wired to the real OpenRouter gateway.

    Search stays on fixtures: M3 is the live *model* milestone, and live web
    search belongs to M4.
    """
    from app.adapters.llm.registry import build_llm_runtime
    from app.adapters.search.fixture import FixtureSearchProvider
    from app.graph.deps import NodeDeps

    settings = Settings(
        _env_file=None,
        app_env="test",
        llm_provider="openrouter",
        llm_api_key=LIVE_API_KEY,
        llm_base_url=LIVE_BASE_URL,
        llm_model_default=LIVE_DEV_MODEL,
        llm_free_models_only=LIVE_FREE_ONLY,
        # Free tiers are rate-limited; hammering them is both rude and
        # counter-productive.
        llm_max_attempts=2 if LIVE_FREE_ONLY else 3,
        search_provider="fixture",
        database_url=migrated_database,
    )
    runtime = await build_llm_runtime(settings)
    engine = create_async_engine(migrated_database, poolclass=NullPool)
    try:
        yield NodeDeps(
            settings=settings,
            session_factory=async_sessionmaker(engine, expire_on_commit=False),
            llm=runtime.client,
            search=FixtureSearchProvider(),
        )
    finally:
        await runtime.client.aclose()
        await engine.dispose()


#: Markers in a provider's 429 body that mean "your daily allowance is gone",
#: as distinct from "slow down".
_QUOTA_EXHAUSTED_MARKERS = (
    "free-models-per-day",
    "per-day",
    "daily limit",
    "quota",
)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item: pytest.Item):  # type: ignore[no-untyped-def]
    """Report an exhausted provider allowance as a skip, not a failure.

    A live test that cannot run because the day's free quota is spent has not
    found a defect; reporting it as a failure buries real ones. A plain rate
    limit still fails, because the adapter is supposed to handle those.
    """
    outcome = yield
    if item.get_closest_marker("live") is None:
        return
    excinfo = getattr(outcome, "excinfo", None)
    if excinfo is None:
        return
    message = str(excinfo[1])
    if "429" not in message and "rate_limit" not in message.lower():
        return
    if not any(marker in message.lower() for marker in _QUOTA_EXHAUSTED_MARKERS):
        return
    outcome.force_exception(
        pytest.skip.Exception(
            "provider free-tier allowance exhausted for today; cannot run live test",
            _use_item_location=True,
        )
    )
