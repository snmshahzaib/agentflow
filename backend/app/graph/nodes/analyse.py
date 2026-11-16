"""Analysis node.

Synthesises findings from retrieved sources. The model sees source *labels*,
never database ids, and every label it returns is resolved against the sources
actually retrieved — anything that does not resolve is dropped here rather
than carried forward (see :mod:`app.graph.binding`).
"""

from __future__ import annotations

import uuid

from sqlalchemy import select

from app.core.logging import get_logger
from app.db.models import Source
from app.domain.nodes import NodeName
from app.graph.binding import bind_source_refs, label_for
from app.graph.deps import NodeDeps
from app.graph.prompts import ANALYST_SYSTEM, SourceForPrompt, render_untrusted_sources
from app.graph.runtime import call_model, record_step
from app.graph.schemas import AnalysisOutput
from app.graph.state import FindingRecord, GraphState

log = get_logger(__name__)


async def load_sources_for_prompt(
    deps: NodeDeps, source_ids: list[uuid.UUID]
) -> list[SourceForPrompt]:
    """Fetch sources in label order, as the model will see them."""
    if not source_ids:
        return []
    async with deps.session_factory() as session:
        rows = (await session.execute(select(Source).where(Source.id.in_(source_ids)))).scalars()
        by_id = {row.id: row for row in rows}

    prompt_sources: list[SourceForPrompt] = []
    for index, source_id in enumerate(source_ids):
        source = by_id.get(source_id)
        if source is None:
            continue
        prompt_sources.append(
            SourceForPrompt(
                label=label_for(index),
                title=source.title or "(untitled)",
                url=source.url,
                excerpt=source.excerpt or "",
            )
        )
    return prompt_sources


async def analyse_node(state: GraphState, deps: NodeDeps) -> GraphState:
    task_id = state["task_id"]
    source_ids = [uuid.UUID(sid) for sid in state.get("source_ids", [])]

    async with record_step(deps, task_id=task_id, node=NodeName.ANALYSE) as step:
        prompt_sources = await load_sources_for_prompt(deps, source_ids)
        step.input_summary = f"{len(prompt_sources)} sources"

        subtasks = state.get("subtasks", [])
        subtask_lines = "\n".join(f"{index}. {question}" for index, question in enumerate(subtasks))
        feedback = state.get("revision_feedback")

        user = (
            f"Research question:\n{state['question']}\n\n"
            f"Subtasks:\n{subtask_lines or '(none)'}\n\n"
            f"{render_untrusted_sources(prompt_sources)}\n\n"
            "Produce findings, each citing the labels of the sources that support it."
        )
        if feedback:
            user += f"\n\nA reviewer asked for a revision with this note:\n{feedback}"

        output = await call_model(
            deps,
            task_id=task_id,
            node=NodeName.ANALYSE,
            schema=AnalysisOutput,
            system=ANALYST_SYSTEM,
            user=user,
        )

        findings: list[FindingRecord] = []
        rejected_total = 0
        for finding in output.findings:
            binding = bind_source_refs(finding.source_refs, source_ids)
            rejected_total += len(binding.rejected)
            findings.append(
                {
                    "statement": finding.statement,
                    "source_ids": [str(sid) for sid in binding.source_ids],
                    "subtask_index": finding.subtask_index,
                }
            )

        step.output = {
            "findings": len(findings),
            "summary": output.summary,
            # Surfaced rather than swallowed: a model citing sources that were
            # never retrieved is worth seeing in the timeline.
            "rejected_source_refs": rejected_total,
        }

    return {"findings": findings}
