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
from app.domain.enums import SearchMode
from app.domain.errors import AdapterError, SearchUnavailableError
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

    # A free search allowance is a shared, exhaustible resource. Capping
    # searches per task means a runaway plan costs one task rather than the
    # month's quota.
    budget = deps.settings.search_max_per_task
    if len(queries) > budget:
        log.warning(
            "search_queries_truncated",
            requested=len(queries),
            budget=budget,
            task_id=task_id,
        )
        queries = queries[:budget]

    async with record_step(deps, task_id=task_id, node=NodeName.RESEARCH) as step:
        step.input_summary = (
            f"{len(queries)} queries via {deps.search.mode.value}, up to {per_subtask} results each"
        )

        retrieved: list[tuple[int, SearchResult]] = []
        failures: list[dict[str, str]] = []

        for index, query in enumerate(queries):
            try:
                results = await deps.search.search(query, max_results=per_subtask)
            except AdapterError as exc:
                # One subtask failing is partial research, not a dead task:
                # the remaining subtasks may still produce usable evidence,
                # and the failure is recorded so the report can disclose it.
                failures.append({"subtask": str(index), "code": exc.code})
                log.warning(
                    "search_subtask_failed",
                    subtask_index=index,
                    code=exc.code,
                    task_id=task_id,
                )
                continue
            retrieved.extend((index, result) for result in results)

        # Every subtask failing is a different matter: there is no evidence,
        # so drafting would produce a report grounded in nothing.
        if failures and not retrieved:
            raise SearchUnavailableError(
                f"Retrieval failed for all {len(queries)} subtask(s); no evidence was gathered.",
                failures=len(failures),
            )

        stored_ids = await _persist_sources(deps, task_uuid, retrieved)

        step.output = {
            "queries": queries,
            "results_retrieved": len(retrieved),
            "sources_stored": len(stored_ids),
            "provider": deps.search.mode.value,
            # Stated explicitly so a reader of the timeline never has to infer
            # whether this came from the live web.
            "is_fixture": deps.search.mode is SearchMode.FIXTURE,
            "subtasks_failed": len(failures),
            "failures": failures or None,
            "partial": bool(failures),
        }

    return {
        "source_ids": [str(sid) for sid in stored_ids],
        "search_failures": len(failures),
    }


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
                score=result.score,
                published_date=result.published_date,
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
