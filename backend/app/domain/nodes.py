"""Graph node identities.

Pure domain types: no framework imports, no configuration, no I/O. The
orchestrator and the LLM adapter both key off these names, which is why they
live here rather than in either of them.
"""

from __future__ import annotations

from enum import StrEnum


class NodeName(StrEnum):
    """The five nodes of the research workflow, in execution order."""

    PLAN = "plan"
    RESEARCH = "research"
    ANALYSE = "analyse"
    CITATION_CHECK = "citation_check"
    DRAFT = "draft"


#: Nodes that call a language model. ``RESEARCH`` is deliberately absent: it
#: queries the search port, and the model is never given network access.
LLM_NODES: frozenset[NodeName] = frozenset(
    {NodeName.PLAN, NodeName.ANALYSE, NodeName.CITATION_CHECK, NodeName.DRAFT}
)

#: Execution order of the graph.
NODE_ORDER: tuple[NodeName, ...] = (
    NodeName.PLAN,
    NodeName.RESEARCH,
    NodeName.ANALYSE,
    NodeName.CITATION_CHECK,
    NodeName.DRAFT,
)
