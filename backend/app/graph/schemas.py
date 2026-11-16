"""Structured output schemas for the research nodes.

Every node returns one of these, validated by Pydantic. Model prose never
becomes control flow — that is layer 3 of the prompt-injection boundary
(docs/security.md), which is why no node parses free text.

Sources are addressed by **reference label** (``S1``, ``S2``, ...) rather than
by database id. The model only ever sees labels for sources we actually
retrieved, and :mod:`app.graph.binding` resolves them back; a label the model
invents cannot survive into a report.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import SupportStatus

#: Shared config: reject unknown fields so a model cannot smuggle extra keys
#: past validation, and keep schemas strict-mode friendly.
_STRICT = ConfigDict(extra="forbid")


class PlannedSubtask(BaseModel):
    model_config = _STRICT

    question: Annotated[str, Field(min_length=5, max_length=300)] = Field(
        description="A single bounded sub-question."
    )
    search_query: Annotated[str, Field(min_length=3, max_length=200)] = Field(
        description="The query to send to the search provider for this subtask."
    )
    rationale: Annotated[str, Field(max_length=500)] = Field(
        default="", description="Why this subtask is needed."
    )


class PlanOutput(BaseModel):
    """Bounded decomposition of the research question."""

    model_config = _STRICT

    subtasks: Annotated[list[PlannedSubtask], Field(min_length=1, max_length=8)]
    rationale: Annotated[str, Field(max_length=1000)] = ""


class Finding(BaseModel):
    model_config = _STRICT

    statement: Annotated[str, Field(min_length=10, max_length=1000)] = Field(
        description="One factual statement drawn from the retrieved sources."
    )
    source_refs: Annotated[list[str], Field(max_length=10)] = Field(
        default_factory=list,
        description="Labels of the sources supporting this statement, e.g. ['S1', 'S3'].",
    )
    subtask_index: Annotated[int, Field(ge=0, le=16)] = 0


class AnalysisOutput(BaseModel):
    """Findings synthesised from retrieved sources."""

    model_config = _STRICT

    findings: Annotated[list[Finding], Field(min_length=1, max_length=40)]
    summary: Annotated[str, Field(max_length=2000)] = ""


class ClaimVerdict(BaseModel):
    """Whether one claim is backed by a retrieved source.

    This is a claim-to-source *support* judgement. It says nothing about
    whether the claim is true, and nothing in this system should describe it
    as a fact check.
    """

    model_config = _STRICT

    claim_text: Annotated[str, Field(min_length=10, max_length=1000)]
    support_status: SupportStatus
    source_refs: Annotated[list[str], Field(max_length=10)] = Field(default_factory=list)
    rationale: Annotated[str, Field(max_length=800)] = ""


class CitationCheckOutput(BaseModel):
    model_config = _STRICT

    verdicts: Annotated[list[ClaimVerdict], Field(min_length=1, max_length=40)]


class ReportSection(BaseModel):
    model_config = _STRICT

    heading: Annotated[str, Field(min_length=2, max_length=200)]
    body: Annotated[str, Field(min_length=10, max_length=6000)]


class DraftOutput(BaseModel):
    """The report body. Citations and limitations are appended by our own
    code from recorded data, not written by the model."""

    model_config = _STRICT

    title: Annotated[str, Field(min_length=5, max_length=200)]
    summary: Annotated[str, Field(min_length=10, max_length=2000)]
    sections: Annotated[list[ReportSection], Field(min_length=1, max_length=12)]
