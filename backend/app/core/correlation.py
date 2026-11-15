"""Correlation and task identifiers carried through async call stacks.

Every log line emitted while handling a request or running a job carries the
same ``correlation_id``, so one research task can be traced across the API
process and the worker process.
"""

from __future__ import annotations

import uuid
from contextvars import ContextVar, Token

_correlation_id: ContextVar[str | None] = ContextVar("correlation_id", default=None)
_task_id: ContextVar[str | None] = ContextVar("task_id", default=None)


def new_correlation_id() -> str:
    return uuid.uuid4().hex


def get_correlation_id() -> str | None:
    return _correlation_id.get()


def set_correlation_id(value: str) -> Token[str | None]:
    return _correlation_id.set(value)


def reset_correlation_id(token: Token[str | None]) -> None:
    _correlation_id.reset(token)


def get_task_id() -> str | None:
    return _task_id.get()


def set_task_id(value: str | None) -> Token[str | None]:
    return _task_id.set(value)


def reset_task_id(token: Token[str | None]) -> None:
    _task_id.reset(token)
