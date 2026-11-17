"""Worker entrypoint.

    arq app.workers.settings.WorkerSettings

Builds the long-lived collaborators once per worker process: the database
engine, the Redis connection, the validated LLM runtime and the LangGraph
Postgres checkpointer. The checkpointer is held open for the worker's
lifetime rather than per job, because opening a connection per node would
make checkpoint writes the slowest part of the graph.
"""

from __future__ import annotations

from typing import Any, ClassVar

from arq import cron
from arq.connections import RedisSettings
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from app.adapters.llm.registry import build_llm_runtime
from app.adapters.search.registry import build_search_provider
from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.db.session import dispose_engine, get_session_factory, init_engine
from app.graph.deps import NodeDeps
from app.workers.jobs import (
    purge_expired_tasks,
    recover_orphaned_tasks,
    resume_task,
    run_task,
)

log = get_logger(__name__)


async def startup(ctx: dict[str, Any]) -> None:
    settings = get_settings()
    configure_logging(level=settings.log_level, fmt=settings.log_format)

    init_engine(settings)
    session_factory = get_session_factory()

    runtime = await build_llm_runtime(settings)
    search = build_search_provider(settings)

    # Held open for the process lifetime; __aenter__/__aexit__ are driven
    # manually because arq's lifecycle is not a context manager.
    saver_cm = AsyncPostgresSaver.from_conn_string(settings.checkpointer_dsn)
    checkpointer = await saver_cm.__aenter__()
    await checkpointer.setup()

    ctx["settings"] = settings
    ctx["session_factory"] = session_factory
    ctx["llm_runtime"] = runtime
    ctx["checkpointer"] = checkpointer
    ctx["_saver_cm"] = saver_cm
    ctx["search"] = search
    ctx["node_deps"] = NodeDeps(
        settings=settings,
        session_factory=session_factory,
        llm=runtime.client,
        search=search,
    )

    log.info(
        "worker_started",
        llm_provider=runtime.provider.value,
        search_provider=settings.search_provider.value,
        offline_mode=settings.is_offline,
        max_concurrent=settings.worker_max_concurrent_tasks,
    )


async def shutdown(ctx: dict[str, Any]) -> None:
    runtime = ctx.get("llm_runtime")
    if runtime is not None:
        await runtime.client.aclose()
    search = ctx.get("search")
    if search is not None:
        await search.aclose()
    saver_cm = ctx.get("_saver_cm")
    if saver_cm is not None:
        await saver_cm.__aexit__(None, None, None)
    await dispose_engine()
    log.info("worker_stopped")


class WorkerSettings:
    """arq configuration."""

    functions: ClassVar[list[Any]] = [
        run_task,
        resume_task,
        recover_orphaned_tasks,
        purge_expired_tasks,
    ]
    cron_jobs: ClassVar[list[Any]] = [
        # Sweeps for tasks whose worker died abruptly. Cheap: it only touches
        # tasks that have looked stalled for longer than the grace period.
        cron(recover_orphaned_tasks, minute=set(range(0, 60, 2)), run_at_startup=True),
        # Retention. Daily at 03:17 rather than on the hour: nothing else is
        # scheduled there, and a deletion sweep should not share a minute with
        # whatever else a deployment runs at midnight. Not run at startup --
        # a restart loop would otherwise purge repeatedly.
        cron(purge_expired_tasks, hour={3}, minute={17}, run_at_startup=False),
    ]
    on_startup = startup
    on_shutdown = shutdown
    # Concurrency is capped so a demo deployment cannot be driven into a
    # model-spend spike by parallel submissions.
    max_jobs = get_settings().worker_max_concurrent_tasks
    job_timeout = 1800
    max_tries = 3
    # Finish the current node and checkpoint before exiting on SIGTERM.
    handle_signals = True
    # Refresh the heartbeat key often enough that `arq --check` -- which the
    # container healthcheck runs -- sees a fresh value. arq's default is an
    # hour, which would report a healthy worker as unhealthy.
    health_check_interval = 15
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
