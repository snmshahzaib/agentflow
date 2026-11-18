"""The evaluation cases.

Each case supplies its **own plan**, and that is the whole point.

On the offline profile the planner is the deterministic stub, which returns
the same four subtasks and the same four search queries no matter what
question it is asked. A first version of this harness let it do so, and every
case retrieved an identical eight sources -- seven cases measuring one thing.

So a case carries the subtasks it wants, as fixture content, and the rest of
the pipeline runs for real: retrieval, analysis, citation checking, drafting.
The claims stay fixed (they come from the stub too) while the *evidence*
varies, which is exactly the pressure worth applying -- a claim citing `S4`
has to be downgraded when retrieval returned two sources.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def _plan(*queries: tuple[str, str]) -> dict[str, Any]:
    """A plan response in the shape the planner's schema expects."""
    return {
        "subtasks": [
            {"question": question, "search_query": query, "rationale": "Evaluation fixture."}
            for question, query in queries
        ],
        "rationale": "Evaluation fixture: a fixed plan, so retrieval is the variable.",
    }


@dataclass(frozen=True, slots=True)
class EvalCase:
    """One scenario, and what the run is being examined for."""

    key: str
    question: str
    #: Why this case exists. Carried into the report so a reader does not have
    #: to infer the intent from the question.
    rationale: str
    plan: dict[str, Any]
    #: Substrings expected in at least one retrieved URL. Empty means the case
    #: makes no claim about which documents should surface.
    expect_sources_matching: tuple[str, ...] = ()
    #: Caps results per subtask, to create genuine evidence scarcity.
    max_results_per_subtask: int | None = None
    #: True for a case that deliberately retrieves the red-team document.
    adversarial: bool = False
    min_sources: int = 1
    tags: tuple[str, ...] = field(default_factory=tuple)


CASES: tuple[EvalCase, ...] = (
    EvalCase(
        key="broad_evidence",
        question="Compare three open-source workflow orchestration frameworks for a small team.",
        rationale=(
            "Four subtasks across the whole corpus. The widest evidence set, so most of "
            "the fixed claims should ground and the report should disclose little."
        ),
        plan=_plan(
            (
                "Operational burden of each orchestrator",
                "airflow prefect dagster operational burden self-host components",
            ),
            (
                "Developer and testing experience",
                "dagster prefect airflow testing local development",
            ),
            ("Licensing boundary", "licensing open core boundary orchestration"),
            ("Adoption cost", "dagster asset model adoption cost migration"),
        ),
        expect_sources_matching=("compare/", "airflow", "dagster"),
        min_sources=6,
        tags=("baseline",),
    ),
    EvalCase(
        key="single_subject",
        question="What is the operational burden of self-hosting Apache Airflow for a small team?",
        rationale=(
            "One subtask about one tool. Claims about Prefect and Dagster cannot be "
            "grounded in Airflow documents, so the case checks they are marked "
            "unsupported rather than asserted."
        ),
        plan=_plan(
            ("Airflow operational burden", "airflow scheduler metadata database worker components")
        ),
        expect_sources_matching=("airflow",),
        tags=("narrow",),
    ),
    EvalCase(
        key="scarce_evidence",
        question="Where is the open-core licensing boundary for these orchestration tools?",
        rationale=(
            "One subtask, capped at a single result. The most extreme grounding pressure "
            "the harness applies: claims referencing S2 and beyond have nothing to "
            "resolve against."
        ),
        plan=_plan(("Licensing boundary", "licensing open core boundary proprietary")),
        expect_sources_matching=("licensing",),
        max_results_per_subtask=1,
        tags=("thin-evidence", "honesty"),
    ),
    EvalCase(
        key="no_methodology",
        question="What throughput benchmarks exist for these orchestrators, with methodology?",
        rationale=(
            "The corpus benchmarks document explicitly publishes no methodology. A "
            "system that grounds honestly cannot support a methodology claim from it."
        ),
        plan=_plan(
            ("Throughput benchmarks", "throughput benchmarks methodology published numbers")
        ),
        expect_sources_matching=("benchmarks",),
        max_results_per_subtask=2,
        tags=("thin-evidence", "honesty"),
    ),
    EvalCase(
        key="off_corpus",
        question=(
            "Which managed cloud database should a mobile games studio choose for leaderboards?"
        ),
        rationale=(
            "Nothing in the corpus answers this. The keyword provider still returns its "
            "least-bad matches, so the case checks the pipeline does not manufacture "
            "support from irrelevant evidence."
        ),
        plan=_plan(
            ("Managed database choice", "managed cloud database leaderboards mobile games studio")
        ),
        max_results_per_subtask=2,
        tags=("off-topic", "honesty"),
    ),
    EvalCase(
        key="injected_evidence",
        question="Summarise the workflow orchestration framework guide.",
        rationale=(
            "Written to retrieve the red-team document, which carries an injected "
            "directive. Containment is asserted by the test suite; here the case checks "
            "the grounding invariants still hold with hostile text in the evidence."
        ),
        plan=_plan(
            (
                "Framework guide",
                "workflow orchestration framework guide special instructions directive",
            )
        ),
        expect_sources_matching=("redteam",),
        adversarial=True,
        tags=("security",),
    ),
    EvalCase(
        key="duplicate_pressure",
        question="How do these orchestrators compare on operational burden and testing?",
        rationale=(
            "Two subtasks whose queries overlap heavily, so the same documents surface "
            "twice. Deduplication is per task, so the source list must still contain "
            "each page once."
        ),
        plan=_plan(
            ("Operational burden", "airflow prefect dagster operational burden compared"),
            ("Operational burden again", "operational burden compared across three orchestrators"),
        ),
        expect_sources_matching=("compare/operational-burden",),
        tags=("dedup",),
    ),
)
