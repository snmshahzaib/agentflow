"""Graph assembly and the durable approval interrupt.

The only module that knows AgentFlow uses LangGraph. Node functions take
``(state, deps)`` and know nothing about the orchestrator, so replacing the
runner would not touch them (ADR-002).

The approval gate is the point of the whole design: ``interrupt()`` ends the
job rather than parking a process on a human, and the resume path picks up
from the last committed checkpoint.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.core.logging import get_logger
from app.domain.enums import ApprovalAction
from app.graph.deps import NodeDeps
from app.graph.nodes.skeleton import (
    analyse_node,
    citation_check_node,
    draft_node,
    plan_node,
    research_node,
)
from app.graph.state import GraphState

log = get_logger(__name__)

AWAIT_APPROVAL = "await_approval"

NodeFn = Callable[[GraphState, NodeDeps], Awaitable[GraphState]]


async def await_approval_node(state: GraphState, deps: NodeDeps) -> GraphState:
    """Pause until a human decides.

    ``interrupt()`` raises out of the graph with the state checkpointed. The
    worker job then finishes: nothing waits, nothing polls, and a full restart
    of every process loses nothing.
    """
    decision: Any = interrupt(
        {
            "task_id": state["task_id"],
            "report_version": state.get("report_version", 1),
            "awaiting": "human_approval",
        }
    )
    if isinstance(decision, dict):
        return {
            "approval_action": str(decision.get("action", ApprovalAction.APPROVE.value)),
            "revision_feedback": decision.get("feedback"),
        }
    return {"approval_action": ApprovalAction.APPROVE.value}


def _bind(fn: NodeFn, deps: NodeDeps) -> Callable[[GraphState], Awaitable[GraphState]]:
    """Adapt a ``(state, deps)`` node to the single-argument shape the graph
    runner expects, without the node importing its dependencies."""

    async def runner(state: GraphState) -> GraphState:
        return await fn(state, deps)

    return runner


def build_graph(deps: NodeDeps, checkpointer: Any | None = None) -> Any:
    """Compile the research graph.

    Dependencies are bound here rather than imported by nodes, which is what
    keeps the node layer orchestrator-agnostic and unit-testable.
    """
    graph: StateGraph[GraphState, None, GraphState, GraphState] = StateGraph(GraphState)

    nodes: dict[str, NodeFn] = {
        "plan": plan_node,
        "research": research_node,
        "analyse": analyse_node,
        "citation_check": citation_check_node,
        "draft": draft_node,
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

    graph.add_edge(START, "plan")
    graph.add_edge("plan", "research")
    graph.add_edge("research", "analyse")
    graph.add_edge("analyse", "citation_check")
    graph.add_edge("citation_check", "draft")
    graph.add_edge("draft", AWAIT_APPROVAL)
    # M1 implements the approval path only. The revision loop
    # (await_approval -> analyse) arrives with real report versioning in M2.
    graph.add_edge(AWAIT_APPROVAL, END)

    return graph.compile(checkpointer=checkpointer)
