"""Report rendering.

The model writes the prose. Citations, the source list and the limitations
section are generated here from recorded run data, so they cannot drift from
what actually happened — a model cannot understate how many claims were
unsupported, or omit that the run used fixtures.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.enums import LLMProvider, SearchMode, SupportStatus

#: Shown when neither the model nor retrieval touched the network.
OFFLINE_NOTICE = (
    "> **Deterministic fixture result — not live AI research.**\n"
    "> This report was produced with a stub model adapter and a committed\n"
    "> fixture corpus. No language model was called and no web search was\n"
    "> performed. Content is repeatable and exists to demonstrate the\n"
    "> workflow, not to inform a real decision."
)

_SUPPORT_LABEL = {
    SupportStatus.SUPPORTED: "supported",
    SupportStatus.PARTIAL: "partially supported",
    SupportStatus.UNSUPPORTED: "**unsupported**",
}


@dataclass(frozen=True, slots=True)
class RenderedClaim:
    text: str
    status: SupportStatus
    source_labels: tuple[str, ...]
    rationale: str


@dataclass(frozen=True, slots=True)
class RenderedSource:
    label: str
    title: str
    url: str
    is_fixture: bool


@dataclass(frozen=True, slots=True)
class ReportProvenance:
    """Everything the limitations section states, taken from recorded data."""

    search_mode: SearchMode
    llm_provider: LLMProvider
    models_served: tuple[str, ...]
    source_count: int
    subtask_count: int
    tokens_used: int
    calls_used: int
    version: int
    revision_count: int


def render_report(
    *,
    title: str,
    summary: str,
    sections: list[tuple[str, str]],
    claims: list[RenderedClaim],
    sources: list[RenderedSource],
    provenance: ReportProvenance,
) -> str:
    """Render the full Markdown report."""
    parts: list[str] = [f"# {title}", ""]

    if provenance.llm_provider is LLMProvider.STUB or provenance.search_mode is SearchMode.FIXTURE:
        parts += [OFFLINE_NOTICE, ""]

    parts += ["## Summary", "", summary.strip(), ""]

    for heading, body in sections:
        parts += [f"## {heading}", "", body.strip(), ""]

    parts += _render_claim_support(claims)
    parts += _render_sources(sources)
    parts += _render_limitations(claims, provenance)

    return "\n".join(parts).rstrip() + "\n"


def _render_claim_support(claims: list[RenderedClaim]) -> list[str]:
    if not claims:
        return []
    lines = [
        "## Claim support",
        "",
        "Each claim below was checked against the sources retrieved for this task.",
        "This checks whether a source *states* the claim — **not** whether the claim",
        "is true. AgentFlow makes no factual-accuracy guarantee.",
        "",
        "| # | Claim | Support | Sources |",
        "|---|---|---|---|",
    ]
    for index, claim in enumerate(claims, start=1):
        cites = ", ".join(claim.source_labels) if claim.source_labels else "—"
        text = claim.text.replace("|", "\\|")
        lines.append(f"| {index} | {text} | {_SUPPORT_LABEL[claim.status]} | {cites} |")
    lines.append("")
    return lines


def _render_sources(sources: list[RenderedSource]) -> list[str]:
    if not sources:
        return ["## Sources", "", "No sources were retrieved for this task.", ""]
    lines = ["## Sources", ""]
    for source in sources:
        marker = " *(fixture)*" if source.is_fixture else ""
        lines.append(f"- **[{source.label}]** [{source.title}]({source.url}){marker}")
    lines.append("")
    return lines


def _render_limitations(claims: list[RenderedClaim], provenance: ReportProvenance) -> list[str]:
    """Generated from actuals, never written by the model."""
    unsupported = sum(1 for c in claims if c.status is SupportStatus.UNSUPPORTED)
    partial = sum(1 for c in claims if c.status is SupportStatus.PARTIAL)

    lines = ["## Limitations", ""]

    if provenance.llm_provider is LLMProvider.STUB:
        lines.append(
            "- Produced by a **deterministic stub adapter**, not a language model. "
            "The reasoning shown is fixture content."
        )
    else:
        served = ", ".join(provenance.models_served) or "unknown"
        lines.append(f"- Written by model(s): `{served}`.")

    if provenance.search_mode is SearchMode.FIXTURE:
        lines.append(
            "- Sources come from a **committed fixture corpus**, not live web search. "
            "They are synthetic documents written for this demonstration."
        )
    else:
        lines.append(
            f"- Sources retrieved via `{provenance.search_mode.value}`; AgentFlow did not "
            "fetch the pages itself and relies on provider-supplied excerpts."
        )

    lines.append(
        f"- Based on {provenance.source_count} source(s) across "
        f"{provenance.subtask_count} subtask(s). Anything those sources do not cover "
        "is absent from this report."
    )

    if unsupported or partial:
        lines.append(
            f"- **{unsupported} claim(s) could not be supported** by any retrieved source "
            f"and {partial} were only partially supported. Treat them as unverified."
        )
    elif claims:
        lines.append(
            f"- All {len(claims)} claim(s) were traced to a retrieved source. That means a "
            "source states them, not that they are correct."
        )

    lines.append(
        "- Claim checking confirms a claim points at a retrieved source. It is **not** a "
        "fact check, and offers no protection against a source being wrong."
    )

    if provenance.revision_count:
        lines.append(
            f"- This is version {provenance.version}, after "
            f"{provenance.revision_count} reviewer revision request(s)."
        )

    lines.append(
        f"- Spend for this task: {provenance.calls_used} model call(s), "
        f"{provenance.tokens_used:,} token(s)."
    )
    lines.append("")
    return lines
