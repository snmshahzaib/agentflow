"""Runs each case through the real graph and collects the facts to score.

The graph, the nodes, the database writes and the citation re-binding are the
production ones; only the model and search providers come from the caller. The
default pairing -- stub model, fixture corpus -- is what makes the run free,
offline and repeatable.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import func, select

from app.adapters.llm.stub import StubLLMClient, load_stub_responses
from app.db.models import ClaimSource, LLMCall, Report, ReportClaim, ResearchTask, Source
from app.domain.nodes import NodeName
from app.graph.binding import label_for
from app.graph.builder import build_graph
from app.graph.deps import NodeDeps
from evals.cases import CASES, EvalCase
from evals.metrics import ClaimFact, Metric, RunFacts, SourceFact, evaluate


@dataclass(frozen=True, slots=True)
class CaseResult:
    case: EvalCase
    facts: RunFacts
    metrics: tuple[Metric, ...]
    retrieval_expectations_met: bool
    missing_expected: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return all(metric.ok for metric in self.metrics) and self.retrieval_expectations_met


async def _create_task(deps: NodeDeps, question: str) -> str:
    async with deps.session_factory() as session, session.begin():
        task = ResearchTask(
            session_id=f"eval-{uuid.uuid4().hex[:8]}",
            question=question,
            search_mode=deps.settings.search_provider,
            llm_provider=deps.settings.llm_provider,
            max_tokens_budget=deps.settings.llm_max_tokens_per_task,
            max_calls_budget=deps.settings.llm_max_calls_per_task,
        )
        session.add(task)
        await session.flush()
        task.graph_thread_id = str(task.id)
        return str(task.id)


async def _collect(deps: NodeDeps, case: EvalCase, task_id: str) -> RunFacts:
    task_uuid = uuid.UUID(task_id)
    async with deps.session_factory() as session:
        task = await session.get(ResearchTask, task_uuid)
        assert task is not None

        sources = list(
            (
                await session.scalars(
                    select(Source).where(Source.task_id == task_uuid).order_by(Source.rank)
                )
            ).all()
        )
        report = await session.scalar(
            select(Report).where(Report.task_id == task_uuid).order_by(Report.version.desc())
        )
        claims = (
            list(
                (
                    await session.scalars(
                        select(ReportClaim)
                        .where(ReportClaim.report_id == report.id)
                        .order_by(ReportClaim.ordinal)
                    )
                ).all()
            )
            if report
            else []
        )
        links = (
            await session.execute(
                select(ClaimSource.claim_id, ClaimSource.source_id).where(
                    ClaimSource.claim_id.in_([c.id for c in claims] or [None])
                )
            )
        ).all()

        calls = int(
            await session.scalar(
                select(func.count()).select_from(LLMCall).where(LLMCall.task_id == task_uuid)
            )
            or 0
        )

    by_claim: dict[Any, list[str]] = {}
    for claim_id, source_id in links:
        by_claim.setdefault(claim_id, []).append(str(source_id))

    # Labels are positional, exactly as the report and the API present them.
    source_facts = tuple(
        SourceFact(
            id=str(source.id),
            label=label_for(index),
            url=source.url,
            url_sha256=source.url_sha256,
            is_fixture=source.is_fixture,
        )
        for index, source in enumerate(sources)
    )
    claim_facts = tuple(
        ClaimFact(
            claim_text=claim.claim_text,
            support_status=claim.support_status.value,
            source_ids=tuple(by_claim.get(claim.id, ())),
        )
        for claim in claims
    )
    # The number of subtasks *researched*, which is what the report states
    # (`len(state["subtasks"])` in the draft node). Deriving it from the
    # stored sources instead gives a different, wrong answer whenever
    # deduplication drops a subtask's results entirely.
    subtasks = len(case.plan.get("subtasks", []))

    return RunFacts(
        case_key=case.key,
        question=case.question,
        sources=source_facts,
        claims=claim_facts,
        report_markdown=report.content_markdown if report else "",
        report_version=report.version if report else 0,
        llm_provider=task.llm_provider.value,
        search_mode=task.search_mode.value,
        model_calls=calls,
        subtask_count=subtasks,
    )


def _deps_for(deps: NodeDeps, case: EvalCase) -> NodeDeps:
    """Give the case its own plan, and its own retrieval cap.

    The stub planner returns the same four search queries whatever it is
    asked, so without a per-case plan every case retrieves identical evidence
    and the suite measures one thing seven times.
    """
    responses = dict(load_stub_responses())
    responses[NodeName.PLAN] = case.plan
    settings = deps.settings
    if case.max_results_per_subtask is not None:
        settings = settings.model_copy(
            update={"search_max_results_per_subtask": case.max_results_per_subtask}
        )
    return replace(deps, llm=StubLLMClient(responses), settings=settings)


async def run_case(deps: NodeDeps, case: EvalCase) -> CaseResult:
    """Execute one case to the approval gate and score it."""
    deps = _deps_for(deps, case)
    task_id = await _create_task(deps, case.question)
    graph = build_graph(deps, checkpointer=InMemorySaver())
    await graph.ainvoke(
        {"task_id": task_id, "question": case.question, "constraints": {}},
        config={"configurable": {"thread_id": task_id}},
    )

    facts = await _collect(deps, case, task_id)
    urls = " ".join(source.url for source in facts.sources).lower()
    missing = tuple(
        pattern for pattern in case.expect_sources_matching if pattern.lower() not in urls
    )
    met = not missing and len(facts.sources) >= case.min_sources

    return CaseResult(
        case=case,
        facts=facts,
        metrics=tuple(evaluate(facts)),
        retrieval_expectations_met=met,
        missing_expected=missing,
    )


async def run_all(deps: NodeDeps, cases: tuple[EvalCase, ...] = CASES) -> list[CaseResult]:
    return [await run_case(deps, case) for case in cases]
