"""Graph state.

A plain ``TypedDict`` with no LangGraph types in it, so the state definition
survives replacing the orchestrator. Node functions take this and return a
partial update; they never mutate it.
"""

from __future__ import annotations

from typing import Any, TypedDict


class FindingRecord(TypedDict):
    """One synthesised finding, with references already resolved to source ids."""

    statement: str
    source_ids: list[str]
    subtask_index: int


class VerdictRecord(TypedDict):
    """One claim-support verdict, with references already resolved."""

    claim_text: str
    support_status: str
    source_ids: list[str]
    rationale: str


class GraphState(TypedDict, total=False):
    """State threaded through the research workflow.

    Deliberately small: bulk content (sources, report text, claims) lives in
    the database, which is authoritative for display and audit. Keeping it out
    of the checkpoint stops the two stores from drifting and keeps checkpoints
    cheap to write.
    """

    task_id: str
    question: str
    constraints: dict[str, Any]

    #: Bounded plan produced by the planner, at most ``MAX_SUBTASKS`` entries.
    subtasks: list[str]
    #: One search query per subtask, produced by the planner.
    search_queries: list[str]
    #: Identifiers of sources persisted by the research node, in label order:
    #: index 0 is the source the model sees as ``S1``.
    source_ids: list[str]
    #: Subtasks whose retrieval failed. Non-zero means the research is
    #: partial, which the report discloses rather than hiding.
    search_failures: int

    #: Working data passed between nodes. Kept here rather than re-read from
    #: the database so a resume after a node does not redo its work; small
    #: enough that checkpoints stay cheap.
    findings: list[FindingRecord]
    verdicts: list[VerdictRecord]

    #: How many revisions a reviewer has requested. The report version is
    #: derived from this, which is what makes the draft node idempotent: a
    #: re-run within the same round targets the same version number.
    revision_round: int
    #: Version of the report the approval gate is waiting on.
    report_version: int

    #: Human decision, supplied when the graph is resumed after an interrupt.
    approval_action: str
    revision_feedback: str | None

    #: Set when a node fails terminally, so the worker can record a reason.
    error_code: str
    error_message: str
