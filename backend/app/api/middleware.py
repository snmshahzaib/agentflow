"""Request-scoped correlation.

Every log line emitted while handling a request carries the same
``correlation_id``, and the response echoes it, so a user-visible error can be
traced to server logs without guesswork.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.correlation import (
    new_correlation_id,
    reset_correlation_id,
    set_correlation_id,
)

CORRELATION_HEADER = "X-Correlation-ID"


class CorrelationMiddleware(BaseHTTPMiddleware):
    """Accept an inbound correlation id or mint one."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        inbound = request.headers.get(CORRELATION_HEADER, "").strip()
        # Bounded and sanitised: an inbound header is untrusted input that
        # would otherwise flow straight into every log line.
        correlation_id = (
            inbound[:64]
            if inbound and inbound.isascii() and inbound.isprintable()
            else new_correlation_id()
        )
        token = set_correlation_id(correlation_id)
        try:
            response = await call_next(request)
        finally:
            reset_correlation_id(token)
        response.headers[CORRELATION_HEADER] = correlation_id
        return response
