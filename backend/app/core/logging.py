"""Structured logging.

JSON in deployed environments, human-readable in a terminal. Two project
rules are enforced here rather than left to discipline at call sites:

* every line carries ``correlation_id`` (and ``task_id`` inside a worker job);
* values whose key looks like a credential are redacted, and prompt or source
  text is never logged by these processors.
"""

from __future__ import annotations

import logging
import re
import sys
from typing import Any

import structlog
from structlog.types import EventDict, Processor

from app.core.correlation import get_correlation_id, get_task_id

#: Keys that contain the word "token" but are measurements, not credentials.
#: The cost ledger depends on these being logged, so they are matched first.
_SAFE_KEYS = frozenset(
    {
        "tokens_in",
        "tokens_out",
        "total_tokens",
        "cache_read_tokens",
        "max_tokens",
        "tokens_used",
        "token_budget",
        "max_tokens_per_task",
        "session_id",
    }
)

#: Key segments that mark a value as a credential. Matched against the key
#: split on separators, so ``tokens_in`` is not caught by ``token``.
_SENSITIVE_SEGMENTS = frozenset(
    {
        "key",
        "apikey",
        "secret",
        "password",
        "passwd",
        "token",
        "credential",
        "credentials",
        "authorization",
        "auth",
        "cookie",
    }
)

#: Compound spellings that never split into segments.
_SENSITIVE_SUBSTRINGS = ("apikey", "authorization", "credential", "password", "secret")

#: ASCII so that it survives JSON escaping and stays greppable in log search.
_REDACTED = "[REDACTED]"

_SEPARATORS = re.compile(r"[_\-.:]+")


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    if lowered in _SAFE_KEYS:
        return False
    if any(part in lowered for part in _SENSITIVE_SUBSTRINGS):
        return True
    return any(seg in _SENSITIVE_SEGMENTS for seg in _SEPARATORS.split(lowered))


def _redact_sensitive(_: Any, __: str, event_dict: EventDict) -> EventDict:
    """Replace values whose key names a credential."""
    for key in list(event_dict):
        if _is_sensitive_key(key):
            event_dict[key] = _REDACTED
    return event_dict


def _add_correlation(_: Any, __: str, event_dict: EventDict) -> EventDict:
    if (cid := get_correlation_id()) is not None:
        event_dict.setdefault("correlation_id", cid)
    if (tid := get_task_id()) is not None:
        event_dict.setdefault("task_id", tid)
    return event_dict


def configure_logging(*, level: str = "info", fmt: str = "json") -> None:
    """Configure structlog and route stdlib logging through it.

    Idempotent: safe to call from both the API and the worker entrypoints.
    """
    shared: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        _add_correlation,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        _redact_sensitive,
    ]

    renderer: Processor = (
        structlog.processors.JSONRenderer()
        if fmt == "json"
        else structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())
    )

    structlog.configure(
        processors=[
            *shared,
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelNamesMapping()[level.upper()]
        ),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )

    # Hand stdlib loggers (uvicorn, sqlalchemy, arq) to structlog so output is
    # one consistent stream rather than two interleaved formats.
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=shared,
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                renderer,
            ],
        )
    )
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())

    for noisy, noisy_level in (
        ("uvicorn.access", logging.WARNING),
        ("sqlalchemy.engine", logging.WARNING),
        ("httpx", logging.WARNING),
        ("httpcore", logging.WARNING),
    ):
        logging.getLogger(noisy).setLevel(noisy_level)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Return a bound logger. Prefer module-level ``log = get_logger(__name__)``."""
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name)
    return logger
