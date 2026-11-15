"""Graph state.

A plain ``TypedDict`` with no LangGraph types in it, so the state definition
survives replacing the orchestrator. Node functions take this and return a
partial update; they never mutate it.
"""

from __future__ import annotations

from typing import Any, TypedDict


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
    #: Identifiers of sources persisted by the research node.
    source_ids: list[str]
    #: Version of the report the approval gate is waiting on.
    report_version: int

    #: Human decision, supplied when the graph is resumed after an interrupt.
    approval_action: str
    revision_feedback: str | None

    #: Set when a node fails terminally, so the worker can record a reason.
    error_code: str
    error_message: str
