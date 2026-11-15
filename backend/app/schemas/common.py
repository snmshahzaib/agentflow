"""Shared response shapes."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ErrorBody(BaseModel):
    code: str = Field(description="Stable machine-readable error code.")
    message: str = Field(description="Human-readable explanation.")
    details: dict[str, Any] = Field(default_factory=dict)
    correlation_id: str | None = Field(
        default=None, description="Identifier shared with server logs for this request."
    )


class ErrorResponse(BaseModel):
    """The single error envelope used by every endpoint."""

    error: ErrorBody


class Page[T](BaseModel):
    """Cursor-paginated collection."""

    items: list[T]
    next_cursor: str | None = None
