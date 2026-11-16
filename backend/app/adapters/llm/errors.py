"""Error normalisation.

Every provider reports failure differently. Node and service code must never
branch on a vendor-specific error shape, so each one is translated here into
the domain hierarchy, where ``retryable`` is the only distinction callers
need.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.domain.errors import (
    AdapterError,
    ProviderAuthError,
    ProviderRateLimitedError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)

#: Status codes that mean "try again", as opposed to "this request is wrong".
_RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504, 522, 524})


#: Never wait longer than this on a provider's say-so; a free tier can report
#: a reset hours away, and a research task should fail fast instead.
MAX_HONOURED_RETRY_AFTER_SECONDS = 60.0


def parse_retry_after(headers: Any) -> float | None:
    """Read a Retry-After hint from provider response headers, if present.

    Accepts the delay-seconds form and the OpenRouter/OpenAI
    `x-ratelimit-reset` variants. A date-form Retry-After is ignored rather
    than guessed at.
    """
    if headers is None:
        return None
    getter = getattr(headers, "get", None)
    if getter is None:
        return None
    for name in ("retry-after", "x-ratelimit-reset-requests", "x-ratelimit-reset"):
        raw = getter(name)
        if raw in (None, ""):
            continue
        try:
            seconds = float(str(raw).rstrip("sS"))
        except ValueError:
            continue
        if seconds <= 0:
            continue
        return min(seconds, MAX_HONOURED_RETRY_AFTER_SECONDS)
    return None


def normalise_http_status(
    status_code: int, *, detail: str | None = None, retry_after: float | None = None
) -> AdapterError:
    """Map an HTTP status from an OpenAI-compatible gateway to a domain error."""
    message = detail or f"The provider returned HTTP {status_code}."
    kwargs: dict[str, Any] = {"status_code": status_code, "retry_after_seconds": retry_after}
    if status_code in (401, 403):
        return ProviderAuthError(message, **kwargs)
    if status_code == 429:
        return ProviderRateLimitedError(message, **kwargs)
    if status_code == 408:
        return ProviderTimeoutError(message, **kwargs)
    if status_code in _RETRYABLE_STATUS or status_code >= 500:
        return ProviderUnavailableError(message, **kwargs)
    return AdapterError(message, **kwargs)


def normalise_exception(exc: BaseException) -> AdapterError:
    """Map a transport or SDK exception to a domain error.

    Matching is structural rather than by import, so this module does not
    depend on a vendor SDK being installed: SDK exceptions expose a
    ``status_code`` attribute, and transport failures are httpx types.
    """
    if isinstance(exc, AdapterError):
        return exc
    if isinstance(exc, httpx.TimeoutException):
        return ProviderTimeoutError(str(exc) or None)
    if isinstance(exc, httpx.ConnectError | httpx.NetworkError | httpx.RemoteProtocolError):
        return ProviderUnavailableError(str(exc) or None)

    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        headers = getattr(getattr(exc, "response", None), "headers", None)
        return normalise_http_status(
            status, detail=str(exc) or None, retry_after=parse_retry_after(headers)
        )

    # An unrecognised failure is treated as retryable-once rather than fatal:
    # a transient provider quirk should not fail a whole research task, and
    # the per-task attempt cap bounds the cost of being wrong.
    return ProviderUnavailableError(f"Unexpected provider failure: {type(exc).__name__}")
