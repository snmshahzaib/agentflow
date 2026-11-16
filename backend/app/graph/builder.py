"""Graph assembly, the approval interrupt and the revision loop.

The only module that knows AgentFlow uses LangGraph. Node functions take
``(state, deps)`` and know nothing about the orchestrator, so replacing the
runner would not touch them (ADR-002).

The approval gate is the point of the design: ``interrupt()`` ends the job
rather than parking a process on a human, and the resume path picks up from
the last committed checkpoint. A revision request re-enters the graph at the
planning node, so a reviewer's note can change the plan and bring in different
sources rather than merely re-wording the same analysis.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.core.logging import get_logger
from app.domain.enums import ApprovalAction
from app.domain.nodes import NodeName
from app.graph.deps import NodeDeps
from app.graph.nodes.analyse import analyse_node
from app.graph.nodes.citation_check import citation_check_node
from app.graph.nodes.draft import draft_node
from app.graph.nodes.plan import plan_node
from app.graph.nodes.research import research_node
from app.graph.state import GraphState

log = get_logger(__name__)

AWAIT_APPROVAL = "await_approval"


class NodeFn(Protocol):
    """A node: state in, partial state delta out. No orchestrator types."""

    async def __call__(self, state: GraphState, deps: NodeDeps) -> GraphState: ...


async def await_approval_node(state: GraphState, deps: NodeDeps) -> GraphState:
    """Pause until a human decides.

    ``interrupt()`` raises out of the graph with state checkpointed. The job
    then finishes: nothing waits, nothing polls, and a full restart of every
    process loses nothing.
    """
    decision: Any = interrupt(
        {
            "task_id": state["task_id"],
            "report_version": state.get("report_version", 1),
            "awaiting": "human_approval",
        }
    )

    if not isinstance(decision, dict):
        return {"approval_action": ApprovalAction.APPROVE.value}

    action = str(decision.get("action", ApprovalAction.APPROVE.value))
    delta: GraphState = {"approval_action": action}

    if action == ApprovalAction.REQUEST_REVISION.value:
        # Reviewer feedback arrives on the trusted operator channel, unlike
        # retrieved source text, so nodes may act on it.
        delta["revision_feedback"] = decision.get("feedback")
        delta["revision_round"] = int(state.get("revision_round", 0)) + 1
        log.info(
            "revision_requested",
            task_id=state["task_id"],
            round=delta["revision_round"],
        )

    return delta


def route_after_approval(state: GraphState) -> str:
    """Approve ends the run; a revision request re-plans."""
    if state.get("approval_action") == ApprovalAction.REQUEST_REVISION.value:
        return NodeName.PLAN.value
    return END


def _bind(fn: NodeFn, deps: NodeDeps) -> Callable[[GraphState], Awaitable[GraphState]]:
    """Adapt a ``(state, deps)`` node to the single-argument shape the runner
    expects, without the node importing its dependencies."""

    async def runner(state: GraphState) -> GraphState:
        return await fn(state, deps)

    return runner


def build_graph(deps: NodeDeps, checkpointer: Any | None = None) -> Any:
    """Compile the research graph."""
    graph: StateGraph[GraphState, None, GraphState, GraphState] = StateGraph(GraphState)

    nodes: dict[str, NodeFn] = {
        NodeName.PLAN.value: plan_node,
        NodeName.RESEARCH.value: research_node,
        NodeName.ANALYSE.value: analyse_node,
        NodeName.CITATION_CHECK.value: citation_check_node,
        NodeName.DRAFT.value: draft_node,
        AWAIT_APPROVAL: await_approval_node,
    }
    for name, fn in nodes.items():
        # LangGraph's add_node overloads do not admit an async callable
        # returning Awaitable[GraphState], though the runtime accepts it (a
        # test compiles this graph and asserts every node is present).
        # warn_unused_ignores is on, so this disappears when upstream stubs
        # are widened.
        graph.add_node(  # type: ignore[call-overload]
            name, _bind(fn, deps), input_schema=GraphState
        )

    graph.add_edge(START, NodeName.PLAN.value)
    graph.add_edge(NodeName.PLAN.value, NodeName.RESEARCH.value)
    graph.add_edge(NodeName.RESEARCH.value, NodeName.ANALYSE.value)
    graph.add_edge(NodeName.ANALYSE.value, NodeName.CITATION_CHECK.value)
    graph.add_edge(NodeName.CITATION_CHECK.value, NodeName.DRAFT.value)
    graph.add_edge(NodeName.DRAFT.value, AWAIT_APPROVAL)
    graph.add_conditional_edges(
        AWAIT_APPROVAL,
        route_after_approval,
        {NodeName.PLAN.value: NodeName.PLAN.value, END: END},
    )

    return graph.compile(checkpointer=checkpointer)
