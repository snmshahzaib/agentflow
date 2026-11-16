"""Dependencies handed to graph nodes.

Nodes receive their collaborators explicitly rather than reaching for module
globals, which is what makes them testable without a running worker and
replaceable without touching the orchestrator.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.llm.base import LLMClient
from app.adapters.search.base import SearchProvider
from app.core.config import Settings


@dataclass(frozen=True, slots=True)
class NodeDeps:
    """Everything a node may use. Nothing else is in scope."""

    settings: Settings
    session_factory: async_sessionmaker[AsyncSession]
    llm: LLMClient
    search: SearchProvider
