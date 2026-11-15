"""Liveness and readiness.

``/health`` answers "is this process up"; ``/ready`` answers "can it serve
traffic", which means the database, Redis and the LLM configuration are all
actually usable. A load balancer needs the first; a deploy gate needs the
second.
"""

from __future__ import annotations

from fastapi import APIRouter, Response, status
from sqlalchemy import text

from app.api.deps import DbDep, OptionalQueueDep, OptionalRuntimeDep
from app.core.logging import get_logger
from app.schemas.meta import HealthResponse, ReadinessComponent, ReadinessResponse

log = get_logger(__name__)
router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse, summary="Liveness probe")
async def health() -> HealthResponse:
    return HealthResponse(status="ok")


@router.get("/ready", response_model=ReadinessResponse, summary="Readiness probe")
async def ready(
    response: Response,
    session: DbDep,
    queue: OptionalQueueDep,
    runtime: OptionalRuntimeDep,
) -> ReadinessResponse:
    components: list[ReadinessComponent] = []

    try:
        await session.execute(text("SELECT 1"))
        components.append(ReadinessComponent(name="database", ready=True))
    except Exception as exc:
        components.append(
            ReadinessComponent(name="database", ready=False, detail=type(exc).__name__)
        )

    if queue is None:
        components.append(ReadinessComponent(name="queue", ready=False, detail="not initialised"))
    else:
        ok = await queue.ping()
        components.append(
            ReadinessComponent(name="queue", ready=ok, detail=None if ok else "unreachable")
        )

    if runtime is None:
        components.append(ReadinessComponent(name="llm", ready=False, detail="not initialised"))
    else:
        # Validation already happened at startup; reaching here means every
        # configured model exists and meets our requirements.
        components.append(
            ReadinessComponent(
                name="llm",
                ready=True,
                detail=f"{runtime.provider.value} ({runtime.catalogue_source})",
            )
        )

    all_ready = all(component.ready for component in components)
    if not all_ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadinessResponse(ready=all_ready, components=components)
