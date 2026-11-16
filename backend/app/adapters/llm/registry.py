"""Provider selection and startup validation.

The single place that turns configuration into a working client. Validation
happens here, at boot, so a bad model ID or an unmet capability is a refused
startup naming the offending environment variable — not a 500 discovered part
way through a user's research task.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.adapters.llm.base import LLMClient, ModelCapabilities
from app.adapters.llm.capabilities import ModelCatalogue, load_catalogue
from app.adapters.llm.openai_compat import OpenAICompatibleLLMClient
from app.adapters.llm.stub import StubLLMClient
from app.core.config import MIN_CONTEXT_TOKENS, Settings
from app.core.logging import get_logger
from app.domain.enums import LLMProvider
from app.domain.errors import ConfigurationError
from app.domain.nodes import NodeName

log = get_logger(__name__)

STUB_MODEL_ID = "stub/deterministic"


@dataclass(frozen=True, slots=True)
class LLMRuntime:
    """A validated client plus everything ``/api/meta`` needs to report."""

    client: LLMClient
    provider: LLMProvider
    models: dict[NodeName, str]
    capabilities: dict[NodeName, ModelCapabilities] = field(default_factory=dict)
    catalogue_source: str = "none"
    catalogue_size: int = 0
    require_structured_output: bool = True
    fallback_models: list[str] = field(default_factory=list)
    free_models_only: bool = False

    @property
    def is_live(self) -> bool:
        return self.provider is not LLMProvider.STUB


async def build_llm_runtime(settings: Settings) -> LLMRuntime:
    """Build and validate the LLM client for the configured provider."""
    if settings.llm_provider is LLMProvider.STUB:
        return await _build_stub_runtime(settings)
    if settings.llm_provider is LLMProvider.OPENROUTER:
        return await _build_gateway_runtime(settings)
    raise ConfigurationError(  # unreachable while LLMProvider has two members
        f"Unsupported LLM_PROVIDER: {settings.llm_provider!r}"
    )


async def _build_stub_runtime(settings: Settings) -> LLMRuntime:
    """Offline runtime. No network, no credentials.

    Configured model IDs are still validated against the committed snapshot
    when present: catching a bad model ID while working offline is better than
    discovering it on the first live run. Nothing is validated when no model
    is configured, which is the default offline state.
    """
    models = dict.fromkeys(sorted(settings.configured_models()), STUB_MODEL_ID)
    capabilities: dict[NodeName, ModelCapabilities] = {}
    source = "stub"

    if settings.llm_model_default.strip():
        catalogue = ModelCatalogue.from_snapshot()
        capabilities = catalogue.validate_node_models(
            settings.configured_models(),
            require_structured_output=settings.llm_require_structured_output,
            min_context_tokens=MIN_CONTEXT_TOKENS,
        )
        source = "snapshot"
        log.info(
            "llm_runtime_ready",
            provider="stub",
            validated_against="snapshot",
            models={n.value: m for n, m in settings.configured_models().items()},
        )
    else:
        log.info("llm_runtime_ready", provider="stub", validated_against="none")

    return LLMRuntime(
        client=StubLLMClient(),
        provider=LLMProvider.STUB,
        models=models,
        capabilities=capabilities,
        catalogue_source=source,
        catalogue_size=len(capabilities),
        require_structured_output=settings.llm_require_structured_output,
    )


async def _build_gateway_runtime(settings: Settings) -> LLMRuntime:
    """Live runtime over an OpenAI-compatible gateway."""
    api_key = settings.llm_api_key.get_secret_value() if settings.llm_api_key else None
    catalogue, source = await load_catalogue(
        base_url=settings.llm_base_url,
        api_key=api_key,
        timeout_seconds=settings.llm_timeout_seconds,
        offline=False,
    )

    configured = settings.configured_models()
    capabilities = catalogue.validate_node_models(
        configured,
        require_structured_output=settings.llm_require_structured_output,
        min_context_tokens=MIN_CONTEXT_TOKENS,
        free_models_only=settings.llm_free_models_only,
    )

    # Fallbacks are validated too, but a broken fallback must not stop the
    # service: it is a degraded-path model, so it is dropped with a warning
    # rather than taking down a working primary.
    usable_fallbacks: list[str] = []
    for model_id in settings.fallback_models:
        caps = catalogue.get(model_id)
        if caps is None:
            log.warning("fallback_model_unknown", model=model_id, source=source)
            continue
        if settings.llm_require_structured_output and not caps.supports_structured_outputs:
            log.warning("fallback_model_lacks_structured_output", model=model_id)
            continue
        if caps.context_length < MIN_CONTEXT_TOKENS:
            log.warning(
                "fallback_model_context_too_small",
                model=model_id,
                context_length=caps.context_length,
            )
            continue
        if settings.llm_free_models_only and not caps.is_free:
            log.warning("fallback_model_not_free", model=model_id)
            continue
        usable_fallbacks.append(model_id)

    if not api_key:  # defence in depth; Settings already refuses this
        raise ConfigurationError("LLM_API_KEY is required for a live provider.")

    client = OpenAICompatibleLLMClient(
        catalogue=catalogue,
        models=configured,
        base_url=settings.llm_base_url,
        api_key=api_key,
        fallback_models=usable_fallbacks,
        require_structured_output=settings.llm_require_structured_output,
        timeout_seconds=settings.llm_timeout_seconds,
        max_attempts=settings.llm_max_attempts,
        data_collection=settings.llm_data_collection,
        free_models_only=settings.llm_free_models_only,
        app_title=settings.llm_app_title,
        app_url=settings.llm_app_url,
        provider=settings.llm_provider,
    )

    log.info(
        "llm_runtime_ready",
        provider=settings.llm_provider.value,
        free_models_only=settings.llm_free_models_only,
        catalogue_source=source,
        catalogue_size=len(catalogue),
        models={n.value: m for n, m in configured.items()},
        fallbacks=usable_fallbacks,
        require_structured_output=settings.llm_require_structured_output,
    )

    return LLMRuntime(
        client=client,
        provider=settings.llm_provider,
        models=configured,
        capabilities=capabilities,
        catalogue_source=source,
        catalogue_size=len(catalogue),
        require_structured_output=settings.llm_require_structured_output,
        fallback_models=usable_fallbacks,
        free_models_only=settings.llm_free_models_only,
    )
