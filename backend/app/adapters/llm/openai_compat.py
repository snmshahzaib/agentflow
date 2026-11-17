"""OpenAI-compatible gateway adapter.

The only module in AgentFlow that imports a vendor SDK — enforced by an
import-linter contract. Because the transport is the OpenAI-compatible Chat
Completions shape, the same adapter serves OpenRouter, a LiteLLM proxy, Azure
OpenAI, Together, Groq or a local vLLM server: only ``LLM_BASE_URL`` and
``LLM_API_KEY`` change.

Three things make this more than a thin wrapper:

* **Capability negotiation.** Structured output is obtained by the strongest
  tier the configured model supports, checked against the catalogue.
* **Cross-vendor fallback.** A retryable failure walks an ordered model chain,
  so one provider's outage degrades model quality instead of failing a task.
* **Truthful accounting.** Every call records the model actually served, the
  tier used, and both our cost estimate and the gateway's reported figure.

The model is never given tools it could use to reach the network: tier 2 uses
a single forced function purely as a schema carrier.
"""

from __future__ import annotations

import asyncio
import json
import random
from collections.abc import Sequence
from typing import Any

from openai import AsyncOpenAI
from pydantic import BaseModel, ValidationError

from app.adapters.llm.base import CallUsage, LLMResult, ModelCapabilities
from app.adapters.llm.capabilities import ModelCatalogue
from app.adapters.llm.errors import normalise_exception
from app.adapters.llm.pricing import estimate_cost_usd
from app.core.logging import get_logger
from app.domain.enums import AttemptOutcome, DataCollectionPolicy, LLMProvider, OutputMode
from app.domain.errors import AdapterError, ConfigurationError, SchemaValidationError
from app.domain.nodes import NodeName

log = get_logger(__name__)

_BACKOFF_BASE_SECONDS = 0.5
_BACKOFF_CAP_SECONDS = 8.0


def strictify_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Make a Pydantic JSON Schema acceptable to strict structured outputs.

    Strict mode requires every object to forbid additional properties and to
    list every property as required. Pydantic omits both for fields with
    defaults, so this walks the schema (including ``$defs``) and tightens it.
    """
    if not isinstance(schema, dict):
        return schema

    out: dict[str, Any] = dict(schema)

    if out.get("type") == "object" or "properties" in out:
        properties = out.get("properties")
        if isinstance(properties, dict):
            out["properties"] = {k: strictify_schema(v) for k, v in properties.items()}
            out["required"] = list(properties)
            out["additionalProperties"] = False

    for key in ("items", "additionalItems", "contains", "not"):
        if key in out:
            out[key] = strictify_schema(out[key])

    for key in ("anyOf", "oneOf", "allOf", "prefixItems"):
        value = out.get(key)
        if isinstance(value, list):
            out[key] = [strictify_schema(v) for v in value]

    defs = out.get("$defs")
    if isinstance(defs, dict):
        out["$defs"] = {k: strictify_schema(v) for k, v in defs.items()}

    return out


class OpenAICompatibleLLMClient:
    """An :class:`~app.adapters.llm.base.LLMClient` over any OpenAI-compatible API."""

    def __init__(
        self,
        *,
        catalogue: ModelCatalogue,
        models: dict[NodeName, str],
        base_url: str,
        api_key: str,
        fallback_models: Sequence[str] = (),
        require_structured_output: bool = True,
        timeout_seconds: float = 90.0,
        max_attempts: int = 3,
        data_collection: DataCollectionPolicy = DataCollectionPolicy.DENY,
        free_models_only: bool = False,
        app_title: str = "AgentFlow",
        app_url: str = "",
        provider: LLMProvider = LLMProvider.OPENROUTER,
    ) -> None:
        self._catalogue = catalogue
        self._models = models
        self._fallbacks = list(fallback_models)
        self._require_schema = require_structured_output
        self._max_attempts = max_attempts
        self._data_collection = data_collection
        self._free_only = free_models_only
        self._provider = provider

        headers: dict[str, str] = {}
        if app_url:
            headers["HTTP-Referer"] = app_url
        if app_title:
            headers["X-Title"] = app_title

        self._client = AsyncOpenAI(
            base_url=base_url,
            api_key=api_key,
            timeout=timeout_seconds,
            max_retries=0,  # retries and backoff are owned here, with the ledger
            default_headers=headers or None,
        )

    @property
    def provider(self) -> LLMProvider:
        return self._provider

    # -- public API ------------------------------------------------------

    async def complete_structured[TOut: BaseModel](
        self,
        *,
        node: NodeName,
        schema: type[TOut],
        system: str,
        user: str,
        max_tokens: int | None = None,
        attempt: int = 1,
    ) -> LLMResult[TOut]:
        """Call the model chain until one returns schema-valid output."""
        chain = self._model_chain(node)
        last_error: AdapterError | None = None
        retried = False

        for chain_index, model_id in enumerate(chain):
            caps = self._catalogue.require(model_id)

            if self._free_only and not caps.is_free:
                # Startup validation already refuses this; checking again at
                # call time means a billable request cannot escape through a
                # client built by other means.
                raise ConfigurationError(
                    f"Refusing to call {model_id}: it is not free and LLM_FREE_MODELS_ONLY is set.",
                    model=model_id,
                )

            mode = caps.output_mode(require_structured_output=self._require_schema)
            if mode is not OutputMode.JSON_SCHEMA:
                # Reachable only with the requirement explicitly relaxed; never
                # let it pass unremarked.
                log.warning(
                    "structured_output_tier_downgraded",
                    node=node.value,
                    model=model_id,
                    tier=mode.value,
                )

            for local_attempt in range(1, self._max_attempts + 1):
                try:
                    result = await self._call_once(
                        node=node,
                        schema=schema,
                        system=system,
                        user=user,
                        caps=caps,
                        mode=mode,
                        max_tokens=max_tokens,
                        attempt=attempt,
                        outcome=self._outcome(chain_index, retried),
                    )
                except AdapterError as exc:
                    last_error = exc
                    log.warning(
                        "llm_call_failed",
                        node=node.value,
                        model=model_id,
                        code=exc.code,
                        retryable=exc.retryable,
                        local_attempt=local_attempt,
                    )
                    if not exc.retryable:
                        break  # a different model may still work; stop retrying this one
                    if local_attempt < self._max_attempts:
                        retried = True
                        # Prefer the provider's own hint over a guess: on a
                        # rate-limited free tier an exponential backoff is
                        # usually wrong in both directions.
                        delay = exc.retry_after_seconds or self._backoff(local_attempt)
                        log.info(
                            "llm_call_retrying",
                            node=node.value,
                            model=model_id,
                            delay_seconds=round(delay, 2),
                            honoured_provider_hint=exc.retry_after_seconds is not None,
                        )
                        await asyncio.sleep(delay)
                        continue
                    break
                else:
                    return result

        raise last_error or AdapterError("No model in the configured chain could be reached.")

    async def aclose(self) -> None:
        await self._client.close()

    # -- internals -------------------------------------------------------

    def _model_chain(self, node: NodeName) -> list[str]:
        """Primary model for the node, then the cross-vendor fallback chain."""
        primary = self._models[node]
        return [primary, *[m for m in self._fallbacks if m != primary]]

    @staticmethod
    def _outcome(chain_index: int, retried: bool) -> AttemptOutcome:
        if chain_index > 0:
            return AttemptOutcome.FELL_BACK
        return AttemptOutcome.RETRIED if retried else AttemptOutcome.OK

    @staticmethod
    def _backoff(attempt: int) -> float:
        """Exponential backoff with full jitter."""
        ceiling = min(_BACKOFF_BASE_SECONDS * 2 ** (attempt - 1), _BACKOFF_CAP_SECONDS)
        return random.uniform(0, ceiling)  # noqa: S311  (not cryptographic)

    def _extra_body(self) -> dict[str, Any]:
        """Gateway routing preferences.

        ``require_parameters`` keeps the request away from providers that
        would silently drop our schema parameter — a model that advertises a
        capability is not the same as a provider that honours it.
        """
        return {
            "provider": {
                "require_parameters": True,
                "data_collection": self._data_collection.value,
            },
            # Ask the gateway to report authoritative cost alongside usage.
            "usage": {"include": True},
        }

    async def _call_once[TOut: BaseModel](
        self,
        *,
        node: NodeName,
        schema: type[TOut],
        system: str,
        user: str,
        caps: ModelCapabilities,
        mode: OutputMode,
        max_tokens: int | None,
        attempt: int,
        outcome: AttemptOutcome,
    ) -> LLMResult[TOut]:
        system_prompt = system
        user_prompt = user
        kwargs: dict[str, Any] = {
            "model": caps.model_id,
            "extra_body": self._extra_body(),
        }
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens

        schema_name = schema.__name__
        json_schema = strictify_schema(schema.model_json_schema())

        if mode is OutputMode.JSON_SCHEMA:
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": schema_name, "strict": True, "schema": json_schema},
            }
        elif mode is OutputMode.TOOL_CALL:
            # A schema carrier, not a capability: the function is never
            # executed and gives the model no access to anything.
            kwargs["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": schema_name,
                        "description": f"Return the result as {schema_name}.",
                        "parameters": json_schema,
                    },
                }
            ]
            kwargs["tool_choice"] = {"type": "function", "function": {"name": schema_name}}
        else:
            user_prompt = (
                f"{user}\n\nReturn only a JSON object matching this schema, with no prose "
                f"and no code fence:\n{json.dumps(json_schema)}"
            )

        started = asyncio.get_running_loop().time()
        try:
            response = await self._client.chat.completions.create(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                **kwargs,
            )
        except Exception as exc:  # normalised, never leaked as a vendor type
            raise normalise_exception(exc) from exc
        latency_ms = int((asyncio.get_running_loop().time() - started) * 1000)

        raw = self._extract_payload(response, mode=mode, schema_name=schema_name)
        try:
            output = schema.model_validate(raw)
        except ValidationError as exc:
            raise SchemaValidationError(
                f"{caps.model_id} returned output that does not satisfy {schema_name}: "
                f"{exc.error_count()} validation error(s).",
                model=caps.model_id,
                schema=schema_name,
            ) from exc

        usage = self._usage(
            response,
            node=node,
            caps=caps,
            mode=mode,
            attempt=attempt,
            outcome=outcome,
            latency_ms=latency_ms,
        )
        return LLMResult(output=output, usage=usage)

    @staticmethod
    def _extract_payload(response: Any, *, mode: OutputMode, schema_name: str) -> Any:
        """Pull the structured payload out of a completion, by tier."""
        choices = getattr(response, "choices", None)
        if not choices:
            raise AdapterError("The provider returned no completion choices.")
        message = choices[0].message

        if mode is OutputMode.TOOL_CALL:
            tool_calls = getattr(message, "tool_calls", None)
            if not tool_calls:
                raise SchemaValidationError(
                    f"The model was asked to call {schema_name} but returned no tool call."
                )
            arguments = tool_calls[0].function.arguments
            return _loads(arguments, schema_name)

        content = getattr(message, "content", None)
        if not content:
            raise SchemaValidationError("The model returned an empty response body.")
        return _loads(content, schema_name)

    def _usage(
        self,
        response: Any,
        *,
        node: NodeName,
        caps: ModelCapabilities,
        mode: OutputMode,
        attempt: int,
        outcome: AttemptOutcome,
        latency_ms: int,
    ) -> CallUsage:
        raw_usage = getattr(response, "usage", None)
        tokens_in = int(getattr(raw_usage, "prompt_tokens", 0) or 0)
        tokens_out = int(getattr(raw_usage, "completion_tokens", 0) or 0)

        details = getattr(raw_usage, "prompt_tokens_details", None)
        cache_read = int(getattr(details, "cached_tokens", 0) or 0)

        # OpenRouter reports authoritative cost when usage accounting is on.
        reported = getattr(raw_usage, "cost", None)
        if reported is None and raw_usage is not None:
            extra = getattr(raw_usage, "model_extra", None) or {}
            reported = extra.get("cost")

        # OpenRouter reports the serving company on the response. Read
        # defensively: it is a gateway extension, not part of the
        # OpenAI-compatible contract.
        provider_served = getattr(response, "provider", None)
        if provider_served is None:
            extra = getattr(response, "model_extra", None) or {}
            provider_served = extra.get("provider")

        return CallUsage(
            node=node,
            provider=self._provider,
            model_requested=caps.model_id,
            # What the gateway actually served, which can differ from what we
            # asked for. Recorded so a report can name the model that wrote it.
            model_served=getattr(response, "model", None) or caps.model_id,
            output_mode=mode,
            attempt=attempt,
            attempt_outcome=outcome,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cache_read_tokens=cache_read,
            latency_ms=latency_ms,
            generation_id=getattr(response, "id", None),
            provider_served=str(provider_served) if provider_served else None,
            cost_usd_estimate=estimate_cost_usd(
                caps, tokens_in=tokens_in, tokens_out=tokens_out, cache_read_tokens=cache_read
            ),
            cost_usd_reported=float(reported) if reported is not None else None,
        )


def _loads(payload: str, schema_name: str) -> Any:
    """Parse JSON, tolerating a code fence that some models still emit."""
    text = payload.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        if text.endswith("```"):
            text = text[:-3].rstrip()
        if text.startswith("json"):
            text = text[4:].lstrip()
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise SchemaValidationError(
            f"The model's response for {schema_name} was not valid JSON.",
            schema=schema_name,
        ) from exc
