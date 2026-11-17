"""Persistence model.

Two stores exist in AgentFlow and their roles do not overlap. These tables are
authoritative for display, audit and every API response. LangGraph's
checkpoint tables (separate ``langgraph`` schema, managed by the library) are
resume plumbing only, and nothing here reads them. Both are written in the
same unit of work, so they cannot diverge. See docs/architecture.md.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin
from app.domain.enums import (
    ApprovalAction,
    AttemptOutcome,
    LLMProvider,
    OutputMode,
    ReportStatus,
    SearchMode,
    StepStatus,
    SupportStatus,
    TaskStatus,
)
from app.domain.nodes import NodeName


def _enum(python_enum: type, name: str) -> Enum:
    """Store enums as VARCHAR + CHECK rather than a native PostgreSQL type.

    Native enums require a migration to add a value and cannot be altered
    inside a transaction on older servers; a checked VARCHAR costs nothing and
    stays easy to evolve.
    """
    return Enum(
        python_enum,
        name=name,
        native_enum=False,
        # SQLAlchemy defaults create_constraint to False, which would leave a
        # bare VARCHAR: any client issuing raw SQL could store a value the
        # application cannot interpret. The CHECK is the actual guarantee.
        create_constraint=True,
        length=32,
        values_callable=lambda e: [m.value for m in e],
        validate_strings=True,
    )


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )


class ResearchTask(Base, TimestampMixin):
    """A research request and its lifecycle.

    ``status`` is coarse on purpose; per-node progress lives in
    ``current_node`` so the API contract survives changes to the graph.
    """

    __tablename__ = "research_tasks"

    id: Mapped[uuid.UUID] = _uuid_pk()

    #: Owning browser session. Scopes visibility without building accounts.
    session_id: Mapped[str] = mapped_column(String(64), nullable=False)
    #: Deduplicates retried submissions (Idempotency-Key header).
    idempotency_key: Mapped[str | None] = mapped_column(String(128))

    question: Mapped[str] = mapped_column(Text, nullable=False)
    constraints_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )

    status: Mapped[TaskStatus] = mapped_column(
        _enum(TaskStatus, "task_status"),
        nullable=False,
        default=TaskStatus.QUEUED,
        index=True,
    )
    current_node: Mapped[NodeName | None] = mapped_column(_enum(NodeName, "node_name"))

    # Provenance: what actually produced this task's content. Written from the
    # resolved configuration, not from the request, and surfaced in the
    # report's limitations section so it cannot drift from reality.
    search_mode: Mapped[SearchMode] = mapped_column(
        _enum(SearchMode, "search_mode"), nullable=False
    )
    llm_provider: Mapped[LLMProvider] = mapped_column(
        _enum(LLMProvider, "llm_provider"), nullable=False
    )

    # Budget: limits captured at creation so a mid-flight config change cannot
    # retroactively alter what a running task was permitted to spend.
    max_tokens_budget: Mapped[int] = mapped_column(Integer, nullable=False)
    max_calls_budget: Mapped[int] = mapped_column(Integer, nullable=False)
    tokens_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    calls_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd_estimate: Mapped[float] = mapped_column(
        Numeric(12, 6), nullable=False, server_default="0"
    )

    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)

    #: LangGraph checkpoint thread. The only link to the orchestrator's state.
    graph_thread_id: Mapped[str | None] = mapped_column(String(64), unique=True)

    #: Soft delete, used by the retention job. Queries filter on NULL.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    steps: Mapped[list[WorkflowStep]] = relationship(
        back_populates="task", cascade="all, delete-orphan", passive_deletes=True
    )
    sources: Mapped[list[Source]] = relationship(
        back_populates="task", cascade="all, delete-orphan", passive_deletes=True
    )
    reports: Mapped[list[Report]] = relationship(
        back_populates="task", cascade="all, delete-orphan", passive_deletes=True
    )
    approval_events: Mapped[list[ApprovalEvent]] = relationship(
        back_populates="task", cascade="all, delete-orphan", passive_deletes=True
    )
    llm_calls: Mapped[list[LLMCall]] = relationship(
        back_populates="task", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (
        UniqueConstraint("session_id", "idempotency_key", name="uq_research_tasks_idempotency"),
        # Text expressions: created_at comes from the mixin and is not in the
        # class namespace when __table_args__ is evaluated.
        Index("ix_research_tasks_session_created", "session_id", text("created_at DESC")),
        Index(
            "ix_research_tasks_active",
            "status",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        CheckConstraint("tokens_used >= 0", name="tokens_used_non_negative"),
        CheckConstraint("calls_used >= 0", name="calls_used_non_negative"),
        CheckConstraint("length(question) between 10 and 2000", name="question_length"),
    )


class WorkflowStep(Base):
    """One attempt at one graph node.

    ``UNIQUE(task_id, node, attempt)`` is what makes worker retries safe: a
    duplicated job cannot create a second row for the same attempt.
    """

    __tablename__ = "workflow_steps"

    id: Mapped[uuid.UUID] = _uuid_pk()
    task_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("research_tasks.id", ondelete="CASCADE"), nullable=False
    )

    node: Mapped[NodeName] = mapped_column(_enum(NodeName, "node_name"), nullable=False)
    status: Mapped[StepStatus] = mapped_column(_enum(StepStatus, "step_status"), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    #: Short human-readable summary. Never the full prompt: prompts can carry
    #: untrusted retrieved content and are not persisted.
    input_summary: Mapped[str | None] = mapped_column(Text)
    output_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)

    task: Mapped[ResearchTask] = relationship(back_populates="steps")

    __table_args__ = (
        UniqueConstraint("task_id", "node", "attempt", name="uq_workflow_steps_task_node_attempt"),
        Index("ix_workflow_steps_task_started", "task_id", "started_at"),
        CheckConstraint("attempt >= 1", name="attempt_positive"),
    )


class Source(Base):
    """A retrieved source. Content is never fetched server-side.

    The search provider returns the excerpt; AgentFlow does not dereference
    the URL, which removes the SSRF surface rather than mitigating it.
    """

    __tablename__ = "sources"

    id: Mapped[uuid.UUID] = _uuid_pk()
    task_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("research_tasks.id", ondelete="CASCADE"), nullable=False
    )

    #: Which planned subtask this source was retrieved for.
    subtask_index: Mapped[int] = mapped_column(Integer, nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    #: Deduplicates the same URL retrieved for several subtasks.
    url_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str | None] = mapped_column(Text)
    excerpt: Mapped[str | None] = mapped_column(Text)

    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    #: True when this came from committed fixtures. Surfaced in the UI; a
    #: fixture is never presented as a live retrieval.
    is_fixture: Mapped[bool] = mapped_column(nullable=False, default=False)

    retrieved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    task: Mapped[ResearchTask] = relationship(back_populates="sources")
    claim_links: Mapped[list[ClaimSource]] = relationship(
        back_populates="source", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (
        UniqueConstraint("task_id", "url_sha256", name="uq_sources_task_url"),
        Index("ix_sources_task_subtask", "task_id", "subtask_index", "rank"),
        CheckConstraint("subtask_index >= 0", name="subtask_index_non_negative"),
    )


class Report(Base):
    """A versioned report. Revisions add a version; they never overwrite one."""

    __tablename__ = "reports"

    id: Mapped[uuid.UUID] = _uuid_pk()
    task_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("research_tasks.id", ondelete="CASCADE"), nullable=False
    )

    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    content_markdown: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[ReportStatus] = mapped_column(
        _enum(ReportStatus, "report_status"), nullable=False, default=ReportStatus.DRAFT
    )
    superseded_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("reports.id", ondelete="SET NULL")
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    task: Mapped[ResearchTask] = relationship(back_populates="reports")
    claims: Mapped[list[ReportClaim]] = relationship(
        back_populates="report", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (
        UniqueConstraint("task_id", "version", name="uq_reports_task_version"),
        CheckConstraint("version >= 1", name="version_positive"),
    )


class ReportClaim(Base):
    """A claim in a report and whether a retrieved source backs it.

    This is a claim-to-source *support* judgement, not a truth judgement.
    Giving it a table rather than burying it in the markdown is what makes
    "unsupported claims are flagged, not silently treated as verified"
    assertable in SQL.
    """

    __tablename__ = "report_claims"

    id: Mapped[uuid.UUID] = _uuid_pk()
    report_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("reports.id", ondelete="CASCADE"), nullable=False
    )

    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    claim_text: Mapped[str] = mapped_column(Text, nullable=False)
    support_status: Mapped[SupportStatus] = mapped_column(
        _enum(SupportStatus, "support_status"), nullable=False
    )
    rationale: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    report: Mapped[Report] = relationship(back_populates="claims")
    source_links: Mapped[list[ClaimSource]] = relationship(
        back_populates="claim", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (
        UniqueConstraint("report_id", "ordinal", name="uq_report_claims_report_ordinal"),
        Index("ix_report_claims_support", "report_id", "support_status"),
    )


class ClaimSource(Base):
    """Evidence linking a claim to a source actually retrieved for the task."""

    __tablename__ = "claim_sources"

    claim_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("report_claims.id", ondelete="CASCADE"), primary_key=True
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE"), primary_key=True
    )

    claim: Mapped[ReportClaim] = relationship(back_populates="source_links")
    source: Mapped[Source] = relationship(back_populates="claim_links")


class ApprovalEvent(Base):
    """An immutable record of a human decision on a specific report version."""

    __tablename__ = "approval_events"

    id: Mapped[uuid.UUID] = _uuid_pk()
    task_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("research_tasks.id", ondelete="CASCADE"), nullable=False
    )

    report_version: Mapped[int] = mapped_column(Integer, nullable=False)
    session_id: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[ApprovalAction] = mapped_column(
        _enum(ApprovalAction, "approval_action"), nullable=False
    )
    feedback: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    task: Mapped[ResearchTask] = relationship(back_populates="approval_events")

    __table_args__ = (Index("ix_approval_events_task_created", "task_id", "created_at"),)


class LLMCall(Base):
    """Per-call ledger: the substrate for honest cost and latency reporting.

    ``model_requested`` and ``model_served`` differ whenever the fallback
    chain fires, so the audit trail stays truthful about which model actually
    produced a report. Both our estimate and the gateway's reported cost are
    kept, because they can legitimately diverge.
    """

    __tablename__ = "llm_calls"

    id: Mapped[uuid.UUID] = _uuid_pk()
    task_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("research_tasks.id", ondelete="CASCADE"), nullable=False
    )

    node: Mapped[NodeName] = mapped_column(_enum(NodeName, "node_name"), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    provider: Mapped[LLMProvider] = mapped_column(
        _enum(LLMProvider, "llm_provider"), nullable=False
    )
    model_requested: Mapped[str] = mapped_column(String(128), nullable=False)
    model_served: Mapped[str | None] = mapped_column(String(128))
    output_mode: Mapped[OutputMode] = mapped_column(_enum(OutputMode, "output_mode"))
    #: Provider-side generation identifier, for reconciling reported cost.
    generation_id: Mapped[str | None] = mapped_column(String(128))
    #: The company that served the request, as reported by the gateway. Kept
    #: because vendor diversity cannot be inferred from model ids: two model
    #: ids may be hosted by the same provider.
    provider_served: Mapped[str | None] = mapped_column(String(64))

    tokens_in: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    latency_ms: Mapped[int | None] = mapped_column(Integer)

    cost_usd_estimate: Mapped[float | None] = mapped_column(Numeric(12, 6))
    cost_usd_reported: Mapped[float | None] = mapped_column(Numeric(12, 6))

    attempt_outcome: Mapped[AttemptOutcome] = mapped_column(
        _enum(AttemptOutcome, "attempt_outcome"), nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    task: Mapped[ResearchTask] = relationship(back_populates="llm_calls")

    __table_args__ = (
        Index("ix_llm_calls_task_created", "task_id", "created_at"),
        CheckConstraint("tokens_in >= 0 and tokens_out >= 0", name="tokens_non_negative"),
    )


class DemoBudget(Base):
    """Daily spend ceiling for the public demo. One row per UTC day."""

    __tablename__ = "demo_budget"

    day: Mapped[datetime] = mapped_column(Date, primary_key=True)
    cost_usd_spent: Mapped[float] = mapped_column(
        Numeric(12, 6), nullable=False, server_default="0"
    )
    tasks_started: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (CheckConstraint("cost_usd_spent >= 0", name="spend_non_negative"),)
