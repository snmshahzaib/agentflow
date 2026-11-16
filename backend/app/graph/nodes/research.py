"""Research node.

Retrieval is performed by this code, not by the model: no node hands the model
a tool, so nothing it emits can reach the network. Sources are persisted as
they are retrieved, deduplicated per task by URL digest.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select

from app.adapters.search.base import SearchResult
from app.core.logging import get_logger
from app.db.models import Source
from app.domain.nodes import NodeName
from app.graph.deps import NodeDeps
from app.graph.runtime import record_step
from app.graph.state import GraphState

log = get_logger(__name__)


async def research_node(state: GraphState, deps: NodeDeps) -> GraphState:
    task_id = state["task_id"]
    task_uuid = uuid.UUID(task_id)
    queries = state.get("search_queries") or [state["question"]]
    per_subtask = deps.settings.search_max_results_per_subtask

    async with record_step(deps, task_id=task_id, node=NodeName.RESEARCH) as step:
        step.input_summary = f"{len(queries)} queries, up to {per_subtask} results each"

        retrieved: list[tuple[int, SearchResult]] = []
        for index, query in enumerate(queries):
            results = await deps.search.search(query, max_results=per_subtask)
            retrieved.extend((index, result) for result in results)

        stored_ids = await _persist_sources(deps, task_uuid, retrieved)

        step.output = {
            "queries": queries,
            "results_retrieved": len(retrieved),
            "sources_stored": len(stored_ids),
            "provider": deps.search.mode.value,
            # Stated explicitly so a reader of the timeline never has to infer
            # whether this came from the live web.
            "is_fixture": deps.search.mode.value == "fixture",
        }

    return {"source_ids": [str(sid) for sid in stored_ids]}


async def _persist_sources(
    deps: NodeDeps, task_uuid: uuid.UUID, retrieved: list[tuple[int, SearchResult]]
) -> list[uuid.UUID]:
    """Store new sources and return every source id for the task, in order.

    Ordering matters: the label the model sees (``S1``, ``S2``, ...) is this
    list's index, so it must be stable across a revision that re-retrieves.
    """
    async with deps.session_factory() as session, session.begin():
        existing = {
            digest: source_id
            for source_id, digest in (
                await session.execute(
                    select(Source.id, Source.url_sha256).where(Source.task_id == task_uuid)
                )
            ).all()
        }

        for subtask_index, result in retrieved:
            digest = result.url_sha256
            if digest in existing:
                continue  # already retrieved for this task, possibly by another subtask
            source = Source(
                task_id=task_uuid,
                subtask_index=subtask_index,
                url=result.url,
                url_sha256=digest,
                title=result.title,
                excerpt=result.excerpt,
                provider=result.provider,
                rank=result.rank,
                is_fixture=result.is_fixture,
            )
            session.add(source)
            await session.flush()
            existing[digest] = source.id

    return await ordered_source_ids(deps, task_uuid)


async def ordered_source_ids(deps: NodeDeps, task_uuid: uuid.UUID) -> list[uuid.UUID]:
    """Every source for a task in the stable order used for labelling."""
    async with deps.session_factory() as session:
        rows = await session.execute(
            select(Source.id)
            .where(Source.task_id == task_uuid)
            .order_by(Source.subtask_index, Source.rank, Source.id)
        )
    return [row[0] for row in rows.all()]
