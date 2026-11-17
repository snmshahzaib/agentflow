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
FULLY_OFFLINE_NOTICE = (
    "> **Deterministic fixture result — not live AI research.**\n"
    "> This report was produced with a stub model adapter and a committed\n"
    "> fixture corpus. No language model was called and no web search was\n"
    "> performed. Content is repeatable and exists to demonstrate the\n"
    "> workflow, not to inform a real decision."
)

#: Shown when a real model reasoned over fixture sources. Distinct from the
#: fully-offline notice because claiming "no language model was called" when
#: one wrote the report would be exactly the false provenance this project
#: exists to avoid.
LIVE_MODEL_FIXTURE_SOURCES_NOTICE = (
    "> **Real model output over fixture sources — not live research.**\n"
    "> A language model genuinely produced the analysis below, but the sources\n"
    "> it reasoned over come from a committed corpus of synthetic documents,\n"
    "> not from live web search. Nothing here reflects the current state of\n"
    "> the world, and it should not inform a real decision."
)

#: Shown when a stub produced the text over genuinely retrieved sources.
STUB_MODEL_LIVE_SOURCES_NOTICE = (
    "> **Fixture text over real sources — not AI research.**\n"
    "> Sources below were genuinely retrieved from the public web, but the\n"
    "> prose was produced by a deterministic stub adapter rather than a\n"
    "> language model."
)

#: Shown when evidence is part live, part fixture. Reachable when retrieval
#: fails for some subtasks, or when a task spans a configuration change.
MIXED_SOURCES_NOTICE = (
    "> **Mixed evidence — part live web, part fixture.**\n"
    "> Some sources below were retrieved from the public web and some come\n"
    "> from a committed corpus of synthetic documents. Each source is marked\n"
    "> individually in the source list. Treat any claim resting on a fixture\n"
    "> source as a demonstration, not as research."
)

#: Shown for a genuinely live run. There is nothing to disclaim about
#: provenance, but there is something to disclaim about reliability: a page
#: being retrievable says nothing about whether it is correct.
LIVE_RESEARCH_NOTICE = (
    "> **Live web research — retrieved, not verified.**\n"
    "> Sources below were retrieved from the public web and a language model\n"
    "> produced the analysis. AgentFlow does not assess whether a retrieved\n"
    "> page is accurate, current, or authoritative; it checks only whether a\n"
    "> claim is supported by something a source actually says."
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
    #: Counted from the sources actually stored, not from configuration. A
    #: task can legitimately hold both: retrieval may fail for one subtask
    #: and succeed for another, and describing such a report by its
    #: configured mode alone would be inaccurate.
    live_source_count: int = 0
    fixture_source_count: int = 0
    #: Subtasks whose retrieval failed, making the research partial.
    search_failures: int = 0


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

    parts += [provenance_notice(provenance), ""]

    parts += ["## Summary", "", summary.strip(), ""]

    for heading, body in sections:
        parts += [f"## {heading}", "", body.strip(), ""]

    parts += _render_claim_support(claims)
    parts += _render_sources(sources)
    parts += _render_limitations(claims, provenance)

    return "\n".join(parts).rstrip() + "\n"


def provenance_notice(provenance: ReportProvenance) -> str:
    """The banner that matches what actually produced this report.

    Derived from the **sources stored**, not from the configured search mode.
    A task whose retrieval partly failed holds both live and fixture
    evidence, and describing it by its configuration would be inaccurate —
    the same class of error as claiming no model was called when one wrote
    the report.

    Every combination gets a notice, including a fully live run: there is
    nothing to disclaim about its provenance, but a page being retrievable
    says nothing about whether it is correct.
    """
    stub_model = provenance.llm_provider is LLMProvider.STUB
    has_live = provenance.live_source_count > 0
    has_fixture = provenance.fixture_source_count > 0

    # No sources at all: fall back to the configured mode, since there is no
    # evidence to describe.
    if not has_live and not has_fixture:
        has_fixture = provenance.search_mode is SearchMode.FIXTURE
        has_live = not has_fixture

    if has_live and has_fixture:
        return MIXED_SOURCES_NOTICE
    if stub_model and has_fixture:
        return FULLY_OFFLINE_NOTICE
    if has_fixture:
        return LIVE_MODEL_FIXTURE_SOURCES_NOTICE
    if stub_model:
        return STUB_MODEL_LIVE_SOURCES_NOTICE
    return LIVE_RESEARCH_NOTICE


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

    if provenance.fixture_source_count and provenance.live_source_count:
        lines.append(
            f"- Evidence is **mixed**: {provenance.live_source_count} source(s) retrieved "
            f"from the web and {provenance.fixture_source_count} from the committed "
            "fixture corpus. Each is marked individually in the source list."
        )
    elif provenance.fixture_source_count or provenance.search_mode is SearchMode.FIXTURE:
        lines.append(
            "- Sources come from a **committed fixture corpus**, not live web search. "
            "They are synthetic documents written for this demonstration."
        )
    else:
        lines.append(
            f"- Sources retrieved via `{provenance.search_mode.value}`. AgentFlow did not "
            "fetch the pages itself; it relies on provider-supplied excerpts, and does "
            "**not** assess whether a page is accurate, current or authoritative."
        )

    if provenance.search_failures:
        lines.append(
            f"- **Retrieval failed for {provenance.search_failures} subtask(s).** This "
            "report is based on partial research, and whatever those subtasks would "
            "have covered is missing."
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
