"""Research task request and response schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import (
    ApprovalAction,
    LLMProvider,
    ReportStatus,
    SearchMode,
    StepStatus,
    SupportStatus,
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


class RevisionRequest(BaseModel):
    """A reviewer sending a report back.

    Feedback is required: a revision with no explanation gives the workflow
    nothing to act on.
    """

    model_config = ConfigDict(extra="forbid")

    feedback: Annotated[str, Field(min_length=3, max_length=2000)] = Field(
        description="What should change. Passed to the planning and drafting steps."
    )


class SourceView(BaseModel):
    """A retrieved source, with the label the model saw it under."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    label: str = Field(description="Reference label used in the report, e.g. 'S1'.")
    subtask_index: int
    url: str
    title: str | None
    excerpt: str | None
    provider: str
    rank: int
    is_fixture: bool = Field(
        description="True when this came from the committed fixture corpus rather than "
        "live retrieval. Fixture sources are never presented as live results."
    )
    retrieved_at: datetime


class ClaimView(BaseModel):
    """A claim and whether a retrieved source backs it.

    `support_status` records whether a source *states* the claim. It is not a
    judgement about whether the claim is true.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    ordinal: int
    claim_text: str
    support_status: SupportStatus
    rationale: str | None
    source_ids: list[UUID]
    source_labels: list[str]


class ReportView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    version: int
    status: ReportStatus
    content_markdown: str
    created_at: datetime


class ApprovalResult(BaseModel):
    task_id: UUID
    action: ApprovalAction
    status: TaskStatus
    report_version: int
