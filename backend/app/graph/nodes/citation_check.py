"""Citation support node.

Decides whether each claim is backed by a retrieved source. This is a
claim-to-source support judgement and nothing more: it makes no assessment of
whether a claim is true, and no part of this system may describe it as a fact
check.

Two containment rules are applied to the model's verdicts here:

* a verdict citing sources that do not resolve has those references dropped;
* a `supported` or `partial` verdict left with no resolvable source is
  **downgraded to unsupported**, because support with no source is not
  support. A model talked into marking everything supported therefore cannot
  manufacture support it cannot evidence.
"""

from __future__ import annotations

import uuid

from app.core.logging import get_logger
from app.domain.enums import SupportStatus
from app.domain.nodes import NodeName
from app.graph.binding import bind_source_refs
from app.graph.deps import NodeDeps
from app.graph.nodes.analyse import load_sources_for_prompt
from app.graph.prompts import CITATION_CHECK_SYSTEM, render_untrusted_sources
from app.graph.runtime import call_model, record_step
from app.graph.schemas import CitationCheckOutput
from app.graph.state import GraphState, VerdictRecord

log = get_logger(__name__)


async def citation_check_node(state: GraphState, deps: NodeDeps) -> GraphState:
    task_id = state["task_id"]
    source_ids = [uuid.UUID(sid) for sid in state.get("source_ids", [])]
    findings = state.get("findings", [])

    async with record_step(deps, task_id=task_id, node=NodeName.CITATION_CHECK) as step:
        prompt_sources = await load_sources_for_prompt(deps, source_ids)
        step.input_summary = f"{len(findings)} claims against {len(prompt_sources)} sources"

        claim_lines = "\n".join(
            f"{index}. {finding['statement']}" for index, finding in enumerate(findings)
        )
        user = (
            f"Research question:\n{state['question']}\n\n"
            f"Claims to check:\n{claim_lines or '(none)'}\n\n"
            f"{render_untrusted_sources(prompt_sources)}\n\n"
            "For each claim, decide whether a retrieved source supports it."
        )

        output = await call_model(
            deps,
            task_id=task_id,
            node=NodeName.CITATION_CHECK,
            schema=CitationCheckOutput,
            system=CITATION_CHECK_SYSTEM,
            user=user,
        )

        verdicts: list[VerdictRecord] = []
        downgraded = 0
        rejected_total = 0

        for verdict in output.verdicts:
            binding = bind_source_refs(verdict.source_refs, source_ids)
            rejected_total += len(binding.rejected)
            status = verdict.support_status

            if status is not SupportStatus.UNSUPPORTED and not binding.source_ids:
                # Support with nothing to point at is not support.
                status = SupportStatus.UNSUPPORTED
                downgraded += 1

            verdicts.append(
                {
                    "claim_text": verdict.claim_text,
                    "support_status": status.value,
                    "source_ids": [str(sid) for sid in binding.source_ids],
                    "rationale": verdict.rationale,
                }
            )

        counts = {
            status.value: sum(1 for v in verdicts if v["support_status"] == status.value)
            for status in SupportStatus
        }
        if downgraded:
            log.warning(
                "verdicts_downgraded_without_sources",
                downgraded=downgraded,
                task_id=task_id,
            )

        step.output = {
            "verdicts": len(verdicts),
            "counts": counts,
            "downgraded_for_missing_sources": downgraded,
            "rejected_source_refs": rejected_total,
        }

    return {"verdicts": verdicts}
