"""Domain enumerations.

``TaskStatus`` is deliberately coarse. Per-node progress lives in
``research_tasks.current_node`` so that the API contract does not change shape
every time the graph gains or loses a node.
"""

from __future__ import annotations

from enum import StrEnum


class TaskStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        """Whether no further work will happen without a new user action."""
        return self in {TaskStatus.APPROVED, TaskStatus.FAILED, TaskStatus.CANCELLED}

    @property
    def is_active(self) -> bool:
        """Whether a client should keep polling."""
        return self in {TaskStatus.QUEUED, TaskStatus.RUNNING}


class StepStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class SupportStatus(StrEnum):
    """Whether a drafted claim is backed by a retrieved source.

    This is a claim-to-source support judgement, **not** a judgement about
    whether the claim is true. See docs/security.md.
    """

    SUPPORTED = "supported"
    PARTIAL = "partial"
    UNSUPPORTED = "unsupported"


class ApprovalAction(StrEnum):
    APPROVE = "approve"
    REQUEST_REVISION = "request_revision"


class ReportStatus(StrEnum):
    DRAFT = "draft"
    APPROVED = "approved"
    SUPERSEDED = "superseded"


class SearchMode(StrEnum):
    FIXTURE = "fixture"
    TAVILY = "tavily"


class LLMProvider(StrEnum):
    STUB = "stub"
    OPENROUTER = "openrouter"


class OutputMode(StrEnum):
    """How schema-constrained output was obtained from a model.

    Tiers are tried in this order; ``PROMPT_JSON`` is refused in live mode
    because it weakens the prompt-injection boundary.
    """

    JSON_SCHEMA = "json_schema"
    TOOL_CALL = "tool_call"
    PROMPT_JSON = "prompt_json"


class AttemptOutcome(StrEnum):
    OK = "ok"
    RETRIED = "retried"
    FELL_BACK = "fell_back"
    FAILED = "failed"


class DataCollectionPolicy(StrEnum):
    DENY = "deny"
    ALLOW = "allow"
