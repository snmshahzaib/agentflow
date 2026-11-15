"""Async engine and session management.

One engine per process. The API process opens a session per request; the
worker opens one per unit of work, which is deliberately the same boundary as
a graph node so that a node's domain rows, its step row and its checkpoint
commit together or not at all.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import Settings

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def create_engine(settings: Settings) -> AsyncEngine:
    """Build an engine. Prefer :func:`get_engine` outside tests."""
    return create_async_engine(
        settings.sqlalchemy_url,
        echo=False,
        pool_pre_ping=True,  # a recycled connection behind a proxy may be dead
        pool_size=5,
        max_overflow=5,
        pool_recycle=1800,
    )


def init_engine(settings: Settings) -> AsyncEngine:
    """Create the process-wide engine and session factory. Idempotent."""
    global _engine, _session_factory
    if _engine is None:
        _engine = create_engine(settings)
        _session_factory = async_sessionmaker(
            _engine,
            expire_on_commit=False,  # objects stay usable after commit
            autoflush=False,
        )
    return _engine


def get_engine() -> AsyncEngine:
    if _engine is None:
        raise RuntimeError("Database engine not initialised; call init_engine() at startup.")
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    if _session_factory is None:
        raise RuntimeError("Session factory not initialised; call init_engine() at startup.")
    return _session_factory


async def dispose_engine() -> None:
    """Close pooled connections. Called on shutdown."""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """A transactional unit of work: commit on success, roll back on error."""
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_db_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency. Yields a session that commits when the request ends."""
    async with session_scope() as session:
        yield session
