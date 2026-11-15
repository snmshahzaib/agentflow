"""API application factory.

Long-lived collaborators are built once at startup: the database engine, the
Redis queue connection and the validated LLM runtime. Model validation happens
here, so a misconfiguration refuses the deployment instead of surfacing as a
500 part way through a user's research task.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.adapters.llm.registry import build_llm_runtime
from app.adapters.queue.arq_queue import ArqTaskQueue
from app.api.errors import register_exception_handlers
from app.api.middleware import CorrelationMiddleware
from app.api.routers import health, meta, tasks
from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.db.session import dispose_engine, init_engine

log = get_logger(__name__)

DESCRIPTION = """
Human-in-the-loop multi-agent research orchestrator.

Submit a research question; specialised graph nodes plan it, retrieve
evidence, check every claim against its sources, and draft a report. The
workflow then pauses for human approval before a report can be exported.

**Honesty note.** When `offline_mode` is true in `/api/meta`, output comes
from committed fixtures and a deterministic stub adapter — not from a live
model. The `citation_check` node verifies that a claim points to a retrieved
source; it does not verify that the claim is true.
"""


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(level=settings.log_level, fmt=settings.log_format)

    init_engine(settings)
    app.state.queue = await ArqTaskQueue.connect(settings.redis_url)
    app.state.llm_runtime = await build_llm_runtime(settings)

    log.info(
        "api_started",
        version=__version__,
        app_env=settings.app_env,
        llm_provider=app.state.llm_runtime.provider.value,
        search_provider=settings.search_provider.value,
        offline_mode=settings.is_offline,
    )
    try:
        yield
    finally:
        await app.state.llm_runtime.client.aclose()
        await app.state.queue.aclose()
        await dispose_engine()
        log.info("api_stopped")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="AgentFlow API",
        version=__version__,
        description=DESCRIPTION,
        lifespan=lifespan,
        # Interactive docs are a demo feature, not a production one.
        docs_url=None if settings.app_env == "production" else "/docs",
        redoc_url=None,
        openapi_url=None if settings.app_env == "production" else "/openapi.json",
    )

    app.add_middleware(CorrelationMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,  # the session cookie must survive the hop
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "Idempotency-Key", "X-Correlation-ID"],
        expose_headers=["X-Correlation-ID", "Location"],
    )

    register_exception_handlers(app)

    app.include_router(health.router)
    app.include_router(meta.router)
    app.include_router(tasks.router)
    return app


app = create_app()
