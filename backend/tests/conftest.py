"""Shared test fixtures.

Tests must never read a developer's real ``.env``: ``make_settings`` builds a
``Settings`` instance with dotenv loading disabled, so every test states its
own configuration explicitly.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import Settings, get_settings

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
