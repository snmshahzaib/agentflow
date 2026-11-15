"""Request dependencies."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Cookie, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.llm.registry import LLMRuntime
from app.adapters.queue.base import TaskQueue
from app.core.config import Settings, get_settings
from app.core.security import (
    SESSION_COOKIE_NAME,
    new_session_id,
    sign_session_id,
    verify_session_cookie,
)
from app.db.session import session_scope
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
