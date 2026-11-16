"""Planning node.

Decomposes the research question into a bounded set of subtasks. The bound is
enforced by us after the model returns, not merely requested in the prompt: a
model that returns twelve subtasks must not be able to turn one research task
into twelve times the retrieval and spend.
"""

from __future__ import annotations

from app.core.logging import get_logger
from app.domain.nodes import NodeName
from app.graph.deps import NodeDeps
from app.graph.prompts import PLANNER_SYSTEM
from app.graph.runtime import call_model, record_step
from app.graph.schemas import PlanOutput
from app.graph.state import GraphState

log = get_logger(__name__)


async def plan_node(state: GraphState, deps: NodeDeps) -> GraphState:
    task_id = state["task_id"]
    question = state["question"]
    limit = deps.settings.max_subtasks

    async with record_step(deps, task_id=task_id, node=NodeName.PLAN) as step:
        step.input_summary = f"question ({len(question)} chars), max {limit} subtasks"

        feedback = state.get("revision_feedback")
        user = f"Research question:\n{question}\n\nProduce at most {limit} subtasks."
        if feedback:
            # Reviewer feedback is operator-channel input, unlike source text.
            user += f"\n\nA reviewer asked for a revision with this note:\n{feedback}"

        output = await call_model(
            deps,
            task_id=task_id,
            node=NodeName.PLAN,
            schema=PlanOutput,
            system=PLANNER_SYSTEM,
            user=user,
        )

        # Enforce the bound ourselves. The prompt asks; this guarantees.
        subtasks = output.subtasks[:limit]
        if len(output.subtasks) > limit:
            log.warning(
                "plan_truncated",
                returned=len(output.subtasks),
                limit=limit,
                task_id=task_id,
            )

        step.output = {
            "subtasks": [
                {"question": s.question, "search_query": s.search_query} for s in subtasks
            ],
            "rationale": output.rationale,
            "truncated": len(output.subtasks) > limit,
        }

    return {
        "subtasks": [s.question for s in subtasks],
        "search_queries": [s.search_query for s in subtasks],
    }
