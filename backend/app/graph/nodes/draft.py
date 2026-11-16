"""Drafting node.

Writes the report and persists it with its claims and evidence links.

Idempotent by construction: the version number is derived from the revision
round rather than from a counter, so re-running this node within the same
round targets the same version and rewrites it instead of creating a second
report. That is what makes a re-delivered or resumed job safe.
"""

from __future__ import annotations

import uuid

from sqlalchemy import delete, select

from app.core.logging import get_logger
from app.db.models import (
    ClaimSource,
    LLMCall,
    Report,
    ReportClaim,
    ResearchTask,
    Source,
)
from app.domain.enums import LLMProvider, ReportStatus, SearchMode, SupportStatus
from app.domain.nodes import NodeName
from app.graph.binding import label_for
from app.graph.deps import NodeDeps
from app.graph.prompts import DRAFT_SYSTEM
from app.graph.report import (
    RenderedClaim,
    RenderedSource,
    ReportProvenance,
    render_report,
)
from app.graph.runtime import call_model, record_step
from app.graph.schemas import DraftOutput
from app.graph.state import GraphState, VerdictRecord

log = get_logger(__name__)


async def draft_node(state: GraphState, deps: NodeDeps) -> GraphState:
    task_id = state["task_id"]
    task_uuid = uuid.UUID(task_id)
    revision_round = int(state.get("revision_round", 0))
    version = revision_round + 1
    verdicts = state.get("verdicts", [])
    source_ids = [uuid.UUID(sid) for sid in state.get("source_ids", [])]

    async with record_step(deps, task_id=task_id, node=NodeName.DRAFT) as step:
        step.input_summary = f"{len(verdicts)} checked claims, writing version {version}"

        supported_lines = []
        for verdict in verdicts:
            status = str(verdict["support_status"])
            supported_lines.append(f"- [{status}] {verdict['claim_text']}")

        user = (
            f"Research question:\n{state['question']}\n\n"
            f"Checked claims:\n{chr(10).join(supported_lines) or '(none)'}\n\n"
            "Write the report. Do not include a limitations section or a "
            "bibliography; both are generated afterwards from recorded data."
        )
        feedback = state.get("revision_feedback")
        if feedback:
            user += f"\n\nA reviewer asked for a revision with this note:\n{feedback}"

        output = await call_model(
            deps,
            task_id=task_id,
            node=NodeName.DRAFT,
            schema=DraftOutput,
            system=DRAFT_SYSTEM,
            user=user,
        )

        markdown = await _render(deps, task_uuid, state, output, version, revision_round)
        await _persist_report(deps, task_uuid, version, markdown, verdicts, source_ids)

        step.output = {
            "report_version": version,
            "claims": len(verdicts),
            "characters": len(markdown),
        }

    return {"report_version": version}


async def _render(
    deps: NodeDeps,
    task_uuid: uuid.UUID,
    state: GraphState,
    output: DraftOutput,
    version: int,
    revision_round: int,
) -> str:
    """Assemble the Markdown, taking provenance from the database."""
    label_by_id = {
        source_id: label_for(index)
        for index, source_id in enumerate(uuid.UUID(s) for s in state.get("source_ids", []))
    }

    async with deps.session_factory() as session:
        task = await session.get(ResearchTask, task_uuid)
        source_rows = (
            (
                await session.execute(
                    select(Source)
                    .where(Source.task_id == task_uuid)
                    .order_by(Source.subtask_index, Source.rank, Source.id)
                )
            )
            .scalars()
            .all()
        )
        # Which models actually produced this task's content, from the
        # ledger -- so the limitations section names what really ran, including
        # any model the fallback chain substituted.
        models_served = tuple(
            dict.fromkeys(
                str(row[0])
                for row in (
                    await session.execute(
                        select(LLMCall.model_served)
                        .where(LLMCall.task_id == task_uuid)
                        .order_by(LLMCall.created_at)
                    )
                ).all()
                if row[0]
            )
        )

    claims = [
        RenderedClaim(
            text=str(verdict["claim_text"]),
            status=SupportStatus(str(verdict["support_status"])),
            source_labels=tuple(
                label_by_id[uuid.UUID(sid)]
                for sid in verdict.get("source_ids", [])
                if uuid.UUID(sid) in label_by_id
            ),
            rationale=str(verdict.get("rationale", "")),
        )
        for verdict in state.get("verdicts", [])
    ]

    sources = [
        RenderedSource(
            label=label_by_id.get(row.id, "S?"),
            title=row.title or row.url,
            url=row.url,
            is_fixture=row.is_fixture,
        )
        for row in source_rows
    ]

    provenance = ReportProvenance(
        search_mode=task.search_mode if task else SearchMode.FIXTURE,
        llm_provider=task.llm_provider if task else LLMProvider.STUB,
        models_served=models_served,
        source_count=len(sources),
        subtask_count=len(state.get("subtasks", [])),
        tokens_used=task.tokens_used if task else 0,
        calls_used=task.calls_used if task else 0,
        version=version,
        revision_count=revision_round,
    )

    return render_report(
        title=output.title,
        summary=output.summary,
        sections=[(section.heading, section.body) for section in output.sections],
        claims=claims,
        sources=sources,
        provenance=provenance,
    )


async def _persist_report(
    deps: NodeDeps,
    task_uuid: uuid.UUID,
    version: int,
    markdown: str,
    verdicts: list[VerdictRecord],
    source_ids: list[uuid.UUID],
) -> None:
    """Write the report, its claims and their evidence links in one transaction.

    Re-running the same version rewrites it rather than inserting a duplicate,
    which is what keeps a resumed or re-delivered job from producing two
    reports for one round.
    """
    valid_source_ids = set(source_ids)

    async with deps.session_factory() as session, session.begin():
        report = await session.scalar(
            select(Report).where(Report.task_id == task_uuid, Report.version == version)
        )

        if report is None:
            report = Report(
                task_id=task_uuid,
                version=version,
                content_markdown=markdown,
                status=ReportStatus.DRAFT,
            )
            session.add(report)
            await session.flush()
        else:
            log.info(
                "report_version_rewritten",
                task_id=str(task_uuid),
                version=version,
                reason="node re-ran within the same revision round",
            )
            report.content_markdown = markdown
            report.status = ReportStatus.DRAFT
            # Claims are rebuilt below, so the old ones must not linger.
            await session.execute(delete(ReportClaim).where(ReportClaim.report_id == report.id))
            await session.flush()

        for ordinal, verdict in enumerate(verdicts):
            claim = ReportClaim(
                report_id=report.id,
                ordinal=ordinal,
                claim_text=str(verdict["claim_text"]),
                support_status=SupportStatus(str(verdict["support_status"])),
                rationale=str(verdict.get("rationale", "")) or None,
            )
            session.add(claim)
            await session.flush()

            linked: set[uuid.UUID] = set()
            for raw in verdict.get("source_ids", []):
                source_id = uuid.UUID(str(raw))
                # Defence in depth: only link sources genuinely retrieved for
                # this task, even though binding already enforced it.
                if source_id in valid_source_ids and source_id not in linked:
                    linked.add(source_id)
                    session.add(ClaimSource(claim_id=claim.id, source_id=source_id))

        # Older versions are history, not candidates for approval.
        previous = (
            (
                await session.execute(
                    select(Report).where(
                        Report.task_id == task_uuid,
                        Report.version < version,
                        Report.status != ReportStatus.SUPERSEDED,
                    )
                )
            )
            .scalars()
            .all()
        )
        for old in previous:
            old.status = ReportStatus.SUPERSEDED
            old.superseded_by = report.id
