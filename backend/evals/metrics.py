"""Metrics over one completed run.

Pure functions over a plain snapshot of what the run produced, so they can be
unit-tested against hand-built inputs without a database, a worker or a model.

Two kinds of figure are reported, and they are not interchangeable:

* **Invariants** must hold. A citation that resolves to something other than a
  source retrieved for that task is a defect, not a low score, and the
  evaluation fails on it.
* **Observations** are reported without a threshold. The proportion of
  unsupported claims depends entirely on how well the evidence happens to
  match the question; a high number is the system being honest about thin
  evidence, not the system failing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SourceFact:
    """One retrieved source, as stored."""

    id: str
    label: str
    url: str
    url_sha256: str
    is_fixture: bool


@dataclass(frozen=True, slots=True)
class ClaimFact:
    """One checked claim, with the sources it was bound to."""

    claim_text: str
    support_status: str
    source_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RunFacts:
    """Everything a metric needs from a completed run."""

    case_key: str
    question: str
    sources: tuple[SourceFact, ...]
    claims: tuple[ClaimFact, ...]
    report_markdown: str
    report_version: int
    llm_provider: str
    search_mode: str
    model_calls: int
    subtask_count: int


@dataclass(frozen=True, slots=True)
class Metric:
    """One measured figure, and whether it is allowed to be anything but perfect."""

    name: str
    value: float | int | bool
    invariant: bool
    detail: str = ""

    @property
    def ok(self) -> bool:
        """Invariants must be exactly 1.0 (or True). Observations always pass."""
        if not self.invariant:
            return True
        if isinstance(self.value, bool):
            return self.value
        return float(self.value) == 1.0


SUPPORTIVE = ("supported", "partial")


def citation_resolution(facts: RunFacts) -> Metric:
    """Every claim→source link must point at a source retrieved for this task.

    This is the integrity property the whole citation design exists to
    guarantee: a model can emit any label it likes, and re-binding drops
    anything that does not resolve. A value below 1.0 means a fabricated
    citation survived into the database.
    """
    retrieved = {source.id for source in facts.sources}
    links = [sid for claim in facts.claims for sid in claim.source_ids]
    if not links:
        return Metric("citation_resolution", 1.0, invariant=True, detail="no citations to check")
    resolved = sum(1 for sid in links if sid in retrieved)
    return Metric(
        "citation_resolution",
        resolved / len(links),
        invariant=True,
        detail=f"{resolved}/{len(links)} links resolve to a retrieved source",
    )


def grounded_support(facts: RunFacts) -> Metric:
    """A claim called supported or partial must carry at least one real source.

    The complement of the rule above: not only must citations resolve, a
    positive verdict must not stand on an empty set. `citation_check`
    downgrades such a claim to unsupported, and this checks it happened.
    """
    positive = [c for c in facts.claims if c.support_status in SUPPORTIVE]
    if not positive:
        return Metric("grounded_support", 1.0, invariant=True, detail="no positive verdicts")
    retrieved = {source.id for source in facts.sources}
    grounded = sum(1 for c in positive if any(sid in retrieved for sid in c.source_ids))
    return Metric(
        "grounded_support",
        grounded / len(positive),
        invariant=True,
        detail=f"{grounded}/{len(positive)} positive verdicts cite a retrieved source",
    )


def source_uniqueness(facts: RunFacts) -> Metric:
    """The same page must not be stored twice for one task."""
    if not facts.sources:
        return Metric("source_uniqueness", 1.0, invariant=True, detail="no sources")
    digests = {source.url_sha256 for source in facts.sources}
    return Metric(
        "source_uniqueness",
        len(digests) / len(facts.sources),
        invariant=True,
        detail=f"{len(digests)} distinct digests across {len(facts.sources)} sources",
    )


def disclosure_accuracy(facts: RunFacts) -> Metric:
    """The limitations section must state the run's real numbers.

    Generated prose that drifts from the data is worse than no prose, because
    a reader has no way to tell. Each check below compares a sentence the
    report asserts against a fact from the database.
    """
    markdown = facts.report_markdown
    unsupported = sum(1 for c in facts.claims if c.support_status == "unsupported")
    checks: list[tuple[str, bool]] = [
        (
            "source count stated",
            f"{len(facts.sources)} source" in markdown,
        ),
        (
            "subtask count stated",
            f"{facts.subtask_count} subtask" in markdown,
        ),
        (
            "unsupported count stated",
            f"**{unsupported} claim(s) could not be supported**" in markdown
            if unsupported
            else "could not be supported" not in markdown or f"**{unsupported} claim" in markdown,
        ),
        (
            "model named",
            facts.llm_provider == "stub" or bool(re.search(r"Written by model\(s\)", markdown)),
        ),
        (
            "search provider named",
            facts.search_mode in markdown or "fixture corpus" in markdown,
        ),
        (
            # The report states its version only once a revision has happened;
            # for a first draft there is no revision history to describe. An
            # earlier version of this check demanded it unconditionally and
            # failed every case against a correct report.
            "version stated when revised",
            facts.report_version == 1 or f"version {facts.report_version}" in markdown,
        ),
        (
            "claim checking not described as fact checking",
            "not** a fact check" in markdown or "not a fact check" in markdown,
        ),
    ]
    failed = [name for name, passed in checks if not passed]
    return Metric(
        "disclosure_accuracy",
        not failed,
        invariant=True,
        detail="all statements match the data" if not failed else f"missing: {', '.join(failed)}",
    )


def provenance_notice(facts: RunFacts) -> Metric:
    """The notice must describe the actual mix of live and fixture evidence."""
    markdown = facts.report_markdown
    live = sum(1 for s in facts.sources if not s.is_fixture)
    fixture = len(facts.sources) - live

    if facts.sources and not live:
        correct = "fixture corpus" in markdown and "retrieved, not verified" not in markdown
        expected = "all-fixture notice"
    elif live and not fixture:
        correct = "retrieved, not verified" in markdown
        expected = "live-retrieval notice"
    else:
        correct = "mixed" in markdown.lower() or "fixture" in markdown
        expected = "mixed-sources notice"

    return Metric(
        "provenance_notice",
        correct,
        invariant=True,
        detail=f"{expected}; {live} live / {fixture} fixture sources",
    )


def unsupported_rate(facts: RunFacts) -> Metric:
    """Observation: how much of the output the system declined to support."""
    if not facts.claims:
        return Metric("unsupported_rate", 0.0, invariant=False, detail="no claims")
    unsupported = sum(1 for c in facts.claims if c.support_status == "unsupported")
    return Metric(
        "unsupported_rate",
        unsupported / len(facts.claims),
        invariant=False,
        detail=f"{unsupported}/{len(facts.claims)} claims unsupported",
    )


def evidence_utilisation(facts: RunFacts) -> Metric:
    """Observation: how much retrieved evidence any claim actually used.

    Low utilisation is not a defect; it means the retrieval was broader than
    the claims needed. It is worth watching because a collapse towards zero
    would suggest retrieval and analysis have stopped talking to each other.
    """
    if not facts.sources:
        return Metric("evidence_utilisation", 0.0, invariant=False, detail="no sources")
    cited = {sid for claim in facts.claims for sid in claim.source_ids}
    used = sum(1 for source in facts.sources if source.id in cited)
    return Metric(
        "evidence_utilisation",
        used / len(facts.sources),
        invariant=False,
        detail=f"{used}/{len(facts.sources)} retrieved sources cited by a claim",
    )


ALL_METRICS = (
    citation_resolution,
    grounded_support,
    source_uniqueness,
    disclosure_accuracy,
    provenance_notice,
    unsupported_rate,
    evidence_utilisation,
)


def evaluate(facts: RunFacts) -> list[Metric]:
    return [metric(facts) for metric in ALL_METRICS]
