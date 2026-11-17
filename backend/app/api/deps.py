"""Request dependencies."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Cookie, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.llm.registry import LLMRuntime
from app.adapters.queue.base import TaskQueue
from app.adapters.ratelimit.base import RateLimiter
from app.core.config import Settings, get_settings
from app.core.security import (
    SESSION_COOKIE_NAME,
    new_session_id,
    sign_session_id,
    verify_session_cookie,
)
from app.db.session import session_scope
from app.domain.errors import RateLimitedError
from app.services.task_service import TaskService

SettingsDep = Annotated[Settings, Depends(get_settings)]


async def get_session(
    settings: SettingsDep,
    response: Response,
    af_session: Annotated[str | None, Cookie()] = None,
) -> str:
    """Resolve the caller's session, minting one on first contact.

    A tampered or unsigned cookie is replaced rather than rejected: there is
    nothing to authenticate, and the signature only needs to stop a visitor
    claiming another visitor's session.
    """
    secret = settings.session_secret.get_secret_value()
    session_id = verify_session_cookie(af_session, secret) if af_session else None
    if session_id is None:
        session_id = new_session_id()
        response.set_cookie(
            SESSION_COOKIE_NAME,
            sign_session_id(session_id, secret),
            httponly=True,
            samesite="lax",
            secure=settings.app_env == "production",
            max_age=60 * 60 * 24 * 30,
        )
    return session_id


SessionDep = Annotated[str, Depends(get_session)]


async def get_db() -> AsyncIterator[AsyncSession]:
    async with session_scope() as session:
        yield session


DbDep = Annotated[AsyncSession, Depends(get_db)]


def get_queue(request: Request) -> TaskQueue:
    queue: TaskQueue = request.app.state.queue
    return queue


def get_rate_limiter(request: Request) -> RateLimiter:
    limiter: RateLimiter = request.app.state.rate_limiter
    return limiter


RateLimiterDep = Annotated[RateLimiter, Depends(get_rate_limiter)]


async def enforce_rate_limit(
    request: Request,
    settings: SettingsDep,
    session_id: SessionDep,
    limiter: RateLimiterDep,
) -> None:
    """Reject a caller who is submitting faster than the configured rate.

    Keyed by session rather than by IP: the session cookie is what scopes
    everything else a visitor can reach, and IP is both shared behind NAT and
    trivially rotated. The method and path are part of the key so that
    approving a report does not consume a submission's allowance.

    Raises ``RateLimitedError`` (HTTP 429) carrying the seconds until the
    window resets.
    """
    key = f"{session_id}:{request.method}:{request.url.path}"
    decision = await limiter.check(key, limit=settings.rate_limit_per_minute, window_seconds=60)
    if not decision.allowed:
        raise RateLimitedError(
            f"Too many requests. Try again in {decision.retry_after_seconds}s.",
            limit=decision.limit,
            window_seconds=60,
            retry_after_seconds=decision.retry_after_seconds,
        )


RateLimited = Depends(enforce_rate_limit)


def get_llm_runtime(request: Request) -> LLMRuntime:
    runtime: LLMRuntime = request.app.state.llm_runtime
    return runtime


QueueDep = Annotated[TaskQueue, Depends(get_queue)]
RuntimeDep = Annotated[LLMRuntime, Depends(get_llm_runtime)]


def get_queue_if_ready(request: Request) -> TaskQueue | None:
    """Readiness variant: absent collaborators are reported, not raised."""
    queue: TaskQueue | None = getattr(request.app.state, "queue", None)
    return queue


def get_llm_runtime_if_ready(request: Request) -> LLMRuntime | None:
    runtime: LLMRuntime | None = getattr(request.app.state, "llm_runtime", None)
    return runtime


OptionalQueueDep = Annotated[TaskQueue | None, Depends(get_queue_if_ready)]
OptionalRuntimeDep = Annotated[LLMRuntime | None, Depends(get_llm_runtime_if_ready)]


def get_task_service(session: DbDep, settings: SettingsDep, queue: QueueDep) -> TaskService:
    return TaskService(session=session, settings=settings, queue=queue)


TaskServiceDep = Annotated[TaskService, Depends(get_task_service)]
