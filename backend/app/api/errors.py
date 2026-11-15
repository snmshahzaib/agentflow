"""HTTP error mapping.

One envelope for every failure: ``{"error": {code, message, details,
correlation_id}}``. Routers never build an error body by hand, so clients can
branch on ``code`` and operators can find the request in the logs by
``correlation_id``.
"""

from __future__ import annotations

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.correlation import get_correlation_id
from app.core.logging import get_logger
from app.domain.errors import (
    AgentFlowError,
    ApprovalRequiredError,
    BudgetExhaustedError,
    ConfigurationError,
    ConflictError,
    NotFoundError,
    RateLimitedError,
    ValidationError,
)

log = get_logger(__name__)

#: Domain error -> HTTP status. Anything unlisted is a 500, which is the safe
#: default: an unmapped error is a bug, not a client problem.
_STATUS_BY_ERROR: list[tuple[type[AgentFlowError], int]] = [
    (NotFoundError, status.HTTP_404_NOT_FOUND),
    (ApprovalRequiredError, status.HTTP_409_CONFLICT),
    (ConflictError, status.HTTP_409_CONFLICT),
    (ValidationError, status.HTTP_422_UNPROCESSABLE_CONTENT),
    (RateLimitedError, status.HTTP_429_TOO_MANY_REQUESTS),
    (BudgetExhaustedError, status.HTTP_409_CONFLICT),
    (ConfigurationError, status.HTTP_503_SERVICE_UNAVAILABLE),
]


def status_for(error: AgentFlowError) -> int:
    for error_type, code in _STATUS_BY_ERROR:
        if isinstance(error, error_type):
            return code
    return status.HTTP_500_INTERNAL_SERVER_ERROR


def error_response(
    *, code: str, message: str, http_status: int, details: dict[str, object] | None = None
) -> JSONResponse:
    return JSONResponse(
        status_code=http_status,
        content={
            "error": {
                "code": code,
                "message": message,
                "details": details or {},
                "correlation_id": get_correlation_id(),
            }
        },
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Install the handlers that guarantee the envelope."""

    @app.exception_handler(AgentFlowError)
    async def _domain_error(_: Request, exc: AgentFlowError) -> JSONResponse:
        http_status = status_for(exc)
        if http_status >= status.HTTP_500_INTERNAL_SERVER_ERROR:
            log.error("request_failed", code=exc.code, message=exc.message, details=exc.details)
        else:
            log.info("request_rejected", code=exc.code, details=exc.details)
        return error_response(
            code=exc.code, message=exc.message, http_status=http_status, details=exc.details
        )

    @app.exception_handler(RequestValidationError)
    async def _request_validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        return error_response(
            code="validation_error",
            message="The request body or parameters were not valid.",
            http_status=status.HTTP_422_UNPROCESSABLE_CONTENT,
            # Pydantic's own structure, which names the offending fields.
            details={"fields": exc.errors()},
        )

    @app.exception_handler(Exception)
    async def _unexpected(_: Request, exc: Exception) -> JSONResponse:
        # Deliberately opaque: an internal failure must not leak a stack
        # trace, a query or a provider message to a client.
        log.exception("unhandled_exception", error=type(exc).__name__)
        return error_response(
            code="internal_error",
            message="An unexpected error occurred.",
            http_status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
