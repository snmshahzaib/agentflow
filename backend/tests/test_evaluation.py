"""The grounding evaluation, enforced.

Two layers. The metric functions are pure, so they are tested against
hand-built inputs including the cases that should fail. The suite itself then
runs every case through the real graph on the offline profile and asserts the
invariants hold -- which is what stops a regression in citation binding or
disclosure from reaching a commit.
"""

from __future__ import annotations

import pytest

from app.graph.deps import NodeDeps
from evals.cases import CASES
from evals.metrics import (
    ClaimFact,
    RunFacts,
    SourceFact,
    citation_resolution,
    disclosure_accuracy,
    evidence_utilisation,
    grounded_support,
    source_uniqueness,
    unsupported_rate,
)
from evals.runner import run_all, run_case


def _source(index: int, *, fixture: bool = True) -> SourceFact:
    return SourceFact(
        id=f"src-{index}",
        label=f"S{index}",
        url=f"https://fixtures.agentflow.test/doc/{index}",
        url_sha256=f"{index:064d}",
        is_fixture=fixture,
    )


def _facts(**overrides: object) -> RunFacts:
    base: dict[str, object] = {
        "case_key": "unit",
        "question": "q",
        "sources": (_source(1), _source(2)),
        "claims": (ClaimFact("a", "supported", ("src-1",)),),
        "report_markdown": "",
        "report_version": 1,
        "llm_provider": "stub",
        "search_mode": "fixture",
        "model_calls": 4,
        "subtask_count": 1,
    }
    base.update(overrides)
    return RunFacts(**base)  # type: ignore[arg-type]


class TestMetricsCatchWhatTheyClaimTo:
    """A metric that cannot fail is not measuring anything."""

    def test_an_unresolvable_citation_fails_the_invariant(self) -> None:
        facts = _facts(claims=(ClaimFact("a", "supported", ("src-does-not-exist",)),))
        metric = citation_resolution(facts)
        assert metric.value == 0.0
        assert not metric.ok

    def test_a_partly_fabricated_citation_set_fails(self) -> None:
        facts = _facts(claims=(ClaimFact("a", "supported", ("src-1", "ghost")),))
        assert citation_resolution(facts).value == 0.5

    def test_a_positive_verdict_with_no_source_fails_the_invariant(self) -> None:
        facts = _facts(claims=(ClaimFact("a", "supported", ()),))
        metric = grounded_support(facts)
        assert metric.value == 0.0
        assert not metric.ok

    def test_an_unsupported_claim_with_no_source_is_fine(self) -> None:
        # That is the honest outcome, not a defect.
        facts = _facts(claims=(ClaimFact("a", "unsupported", ()),))
        assert grounded_support(facts).ok
        assert unsupported_rate(facts).value == 1.0

    def test_a_duplicated_source_fails_uniqueness(self) -> None:
        duplicate = SourceFact(
            id="src-2", label="S2", url="x", url_sha256=_source(1).url_sha256, is_fixture=True
        )
        metric = source_uniqueness(_facts(sources=(_source(1), duplicate)))
        assert metric.value == 0.5
        assert not metric.ok

    def test_disclosure_fails_when_the_report_states_the_wrong_counts(self) -> None:
        metric = disclosure_accuracy(
            _facts(report_markdown="Based on 99 source(s) across 7 subtask(s).")
        )
        assert not metric.ok
        assert "missing" in metric.detail

    def test_disclosure_passes_on_a_report_that_states_the_truth(self) -> None:
        facts = _facts(
            sources=(_source(1), _source(2)),
            subtask_count=1,
            claims=(ClaimFact("a", "supported", ("src-1",)),),
            report_markdown=(
                "Based on 2 source(s) across 1 subtask(s). Sources retrieved via "
                "`fixture`. Claim checking is **not** a fact check."
            ),
        )
        assert disclosure_accuracy(facts).ok

    def test_utilisation_is_an_observation_not_an_invariant(self) -> None:
        # Nothing cited, so utilisation is zero -- and that still passes,
        # because broad retrieval is not a defect.
        metric = evidence_utilisation(_facts(claims=(ClaimFact("a", "unsupported", ()),)))
        assert metric.value == 0.0
        assert metric.ok


@pytest.mark.integration
class TestTheSuiteHolds:
    """Every case, through the real graph, on the offline profile."""

    async def test_every_case_passes_every_invariant(self, node_deps: NodeDeps) -> None:
        results = await run_all(node_deps)

        failures = [
            f"{result.case.key}: {metric.name} -- {metric.detail}"
            for result in results
            for metric in result.metrics
            if not metric.ok
        ]
        assert failures == []

        unmet = [r.case.key for r in results if not r.retrieval_expectations_met]
        assert unmet == []

    async def test_scarce_evidence_raises_the_unsupported_rate(self, node_deps: NodeDeps) -> None:
        """The headline finding, asserted rather than left to a report.

        The stub's claims are fixed, so when retrieval returns one source the
        claims citing later labels cannot be grounded -- and must be marked
        unsupported instead of asserted. This is the behaviour the whole
        citation design exists to produce.
        """
        broad = next(case for case in CASES if case.key == "broad_evidence")
        scarce = next(case for case in CASES if case.key == "scarce_evidence")

        broad_result = await run_case(node_deps, broad)
        scarce_result = await run_case(node_deps, scarce)

        def unsupported(result: object) -> float:
            metrics = {m.name: m for m in result.metrics}  # type: ignore[attr-defined]
            return float(metrics["unsupported_rate"].value)

        assert len(scarce_result.facts.sources) < len(broad_result.facts.sources)
        assert unsupported(scarce_result) > unsupported(broad_result)
        # And integrity is not traded away to achieve it.
        for result in (broad_result, scarce_result):
            metrics = {m.name: m for m in result.metrics}
            assert metrics["citation_resolution"].value == 1.0
            assert metrics["grounded_support"].value == 1.0

    async def test_hostile_evidence_does_not_break_grounding(self, node_deps: NodeDeps) -> None:
        case = next(case for case in CASES if case.adversarial)
        result = await run_case(node_deps, case)

        # The red-team document really is in the evidence...
        assert any("redteam" in source.url for source in result.facts.sources)
        # ...and every invariant still holds.
        assert all(metric.ok for metric in result.metrics)
