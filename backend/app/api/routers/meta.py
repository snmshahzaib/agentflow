"""Runtime metadata.

Exists so nobody has to guess what produced a result: the resolved model for
every node, where the catalogue came from, and whether the system is running
offline on fixtures and a stub.
"""

from __future__ import annotations

from fastapi import APIRouter

from app import __version__
from app.api.deps import RuntimeDep, SettingsDep
from app.schemas.meta import MetaResponse, NodeModelInfo

router = APIRouter(prefix="/api", tags=["meta"])


@router.get("/meta", response_model=MetaResponse, summary="Resolved runtime configuration")
async def meta(settings: SettingsDep, runtime: RuntimeDep) -> MetaResponse:
    models: list[NodeModelInfo] = []
    for node, model_id in sorted(runtime.models.items()):
        caps = runtime.capabilities.get(node)
        models.append(
            NodeModelInfo(
                node=node.value,
                model=model_id,
                context_length=caps.context_length if caps else None,
                supports_structured_outputs=(caps.supports_structured_outputs if caps else None),
                prompt_usd_per_million=caps.prompt_usd_per_million if caps else None,
                completion_usd_per_million=caps.completion_usd_per_million if caps else None,
            )
        )

    return MetaResponse(
        version=__version__,
        app_env=settings.app_env,
        llm_provider=runtime.provider,
        search_provider=settings.search_provider,
        offline_mode=settings.is_offline,
        catalogue_source=runtime.catalogue_source,
        catalogue_size=runtime.catalogue_size,
        require_structured_output=runtime.require_structured_output,
        models=models,
        fallback_models=runtime.fallback_models,
        max_subtasks=settings.max_subtasks,
        max_tokens_per_task=settings.llm_max_tokens_per_task,
        max_calls_per_task=settings.llm_max_calls_per_task,
    )
