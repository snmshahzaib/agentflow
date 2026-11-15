"""Research task request and response schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import (
    ApprovalAction,
    LLMProvider,
    SearchMode,
    StepStatus,
    TaskStatus,
)
from app.domain.nodes import NodeName


class TaskCreate(BaseModel):
    """A new research request."""

    model_config = ConfigDict(extra="forbid")

    question: Annotated[str, Field(min_length=10, max_length=2000)] = Field(
        description="The research objective, in plain language.",
        examples=["Compare three open-source workflow orchestration frameworks."],
    )
    constraints: dict[str, Any] = Field(
        default_factory=dict,
        description="Optional constraints, e.g. {'recency_years': 2}.",
    )


class TaskSummary(BaseModel):
    """List-view projection of a task."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    question: str
    status: TaskStatus
    current_node: NodeName | None
    created_at: datetime
    updated_at: datetime


class TaskDetail(TaskSummary):
    """Single-task view, including provenance and spend."""

    constraints_json: dict[str, Any]
    search_mode: SearchMode
    llm_provider: LLMProvider
    tokens_used: int
    calls_used: int
    max_tokens_budget: int
    max_calls_budget: int
    cost_usd_estimate: float
    error_code: str | None
    error_message: str | None


class StepView(BaseModel):
    """One attempt at one graph node, for the workflow timeline."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    node: NodeName
    status: StepStatus
    attempt: int
    input_summary: str | None
    output_json: dict[str, Any] | None
    error: str | None
    started_at: datetime
    finished_at: datetime | None
    duration_ms: int | None


class ApprovalRequest(BaseModel):
    """A human decision on the current report version."""

    model_config = ConfigDict(extra="forbid")

    feedback: Annotated[str | None, Field(max_length=2000)] = None


class ApprovalResult(BaseModel):
    task_id: UUID
    action: ApprovalAction
    status: TaskStatus
    report_version: int
