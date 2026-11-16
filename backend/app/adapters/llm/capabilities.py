"""Model catalogue loading and startup validation.

Capability is **not** uniform across a multi-vendor catalogue: context
windows, structured-output support and pricing all vary by model and
sometimes by serving provider. This module turns that variability into a
single fail-fast check at boot, so a misconfiguration surfaces as a refused
startup naming the offending environment variable — not as a 500 three nodes
into a user's research task.

The catalogue is fetched live when a live provider is configured, with a
committed snapshot as the offline fallback so tests and CI never touch the
network.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import httpx

from app.adapters.llm.base import ModelCapabilities
from app.core.config import NODE_MODEL_ENV_VARS
from app.core.logging import get_logger
from app.domain.errors import ModelCapabilityError, ModelNotAvailableError
from app.domain.nodes import NodeName

log = get_logger(__name__)

SNAPSHOT_PATH = Path(__file__).with_name("catalogue_snapshot.json")

#: Catalogue flags that indicate native JSON-Schema enforcement.
_STRUCTURED_FLAGS = ("structured_outputs", "response_format")
_TOOL_FLAGS = ("tools", "tool_choice")


def _coerce_price(raw: Any) -> float:
    """Catalogues publish prices as strings; absent or negative means unknown.

    A negative price is how some catalogues mark dynamic routing entries,
    which must never be selected for a node.
    """
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return -1.0
    return value if value >= 0 else -1.0


def _all_prices_zero(pricing: Mapping[str, Any]) -> bool:
    """Whether every published pricing field is zero.

    Checked across the whole pricing map rather than just the token prices: a
    non-zero request or image fee would make a nominally free model billable,
    and the free-only guard must not be fooled by that.
    """
    if not pricing:
        return False
    for value in pricing.values():
        try:
            if float(value) != 0.0:
                return False
        except (TypeError, ValueError):
            continue
    return True


class ModelCatalogue:
    """An immutable view of the models a provider offers."""

    def __init__(self, entries: Mapping[str, ModelCapabilities]) -> None:
        self._entries = dict(entries)

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, model_id: str) -> bool:
        return model_id in self._entries

    def get(self, model_id: str) -> ModelCapabilities | None:
        return self._entries.get(model_id)

    def require(self, model_id: str) -> ModelCapabilities:
        caps = self._entries.get(model_id)
        if caps is None:
            raise ModelNotAvailableError(
                f"Model {model_id!r} is not in the provider catalogue "
                f"({len(self._entries)} models known). Check the model ID, or the provider "
                "may have renamed or retired it — see docs/model-configuration.md.",
                model=model_id,
            )
        return caps

    # -- construction ----------------------------------------------------

    @classmethod
    def from_entries(cls, raw: Mapping[str, Mapping[str, Any]]) -> ModelCatalogue:
        """Build from the snapshot shape: ``{model_id: {...}}``."""
        entries: dict[str, ModelCapabilities] = {}
        for model_id, body in raw.items():
            params = {str(p) for p in body.get("supported_parameters", [])}
            pricing = body.get("pricing", {})
            is_free = _all_prices_zero(pricing)
            entries[model_id] = ModelCapabilities(
                model_id=model_id,
                context_length=int(body.get("context_length") or 0),
                supports_structured_outputs=any(f in params for f in _STRUCTURED_FLAGS),
                supports_tools=any(f in params for f in _TOOL_FLAGS),
                prompt_usd_per_token=_coerce_price(pricing.get("prompt")),
                completion_usd_per_token=_coerce_price(pricing.get("completion")),
                is_free=is_free,
            )
        return cls(entries)

    @classmethod
    def from_provider_payload(cls, payload: Mapping[str, Any]) -> ModelCatalogue:
        """Build from an OpenAI-compatible ``GET /models`` response body."""
        raw: dict[str, dict[str, Any]] = {}
        for item in payload.get("data", []):
            model_id = item.get("id")
            if isinstance(model_id, str):
                raw[model_id] = dict(item)
        return cls.from_entries(raw)

    @classmethod
    def from_snapshot(cls, path: Path | None = None) -> ModelCatalogue:
        """Load the committed snapshot. Never touches the network."""
        target = path or SNAPSHOT_PATH
        with target.open(encoding="utf-8") as handle:
            return cls.from_entries(json.load(handle))

    # -- validation ------------------------------------------------------

    def validate_node_models(
        self,
        models: Mapping[NodeName, str],
        *,
        require_structured_output: bool,
        min_context_tokens: int,
        free_models_only: bool = False,
    ) -> dict[NodeName, ModelCapabilities]:
        """Check every configured model, or raise naming what is wrong.

        Deliberately checks *all* nodes before raising nothing-by-halves: a
        developer fixing configuration wants the full list, not one error per
        restart.
        """
        resolved: dict[NodeName, ModelCapabilities] = {}
        problems: list[str] = []

        for node in sorted(models):
            model_id = models[node]
            env_var = NODE_MODEL_ENV_VARS.get(node, "LLM_MODEL_DEFAULT")

            if not model_id:
                problems.append(
                    f"{node.value}: no model configured (set {env_var} or LLM_MODEL_DEFAULT)"
                )
                continue

            caps = self.get(model_id)
            if caps is None:
                problems.append(
                    f"{node.value}: model {model_id!r} is not in the provider catalogue ({env_var})"
                )
                continue

            if caps.context_length < min_context_tokens:
                problems.append(
                    f"{node.value}: {model_id} has a {caps.context_length:,}-token context window, "
                    f"below the {min_context_tokens:,} minimum ({env_var})"
                )
            if require_structured_output and not caps.supports_structured_outputs:
                problems.append(
                    f"{node.value}: {model_id} does not support native structured outputs, and "
                    "LLM_REQUIRE_STRUCTURED_OUTPUT is true. Schema-constrained output is part of "
                    "the prompt-injection boundary (docs/security.md); choose another model rather "
                    "than relaxing the setting."
                )
            elif not caps.supports_structured_outputs and not caps.supports_tools:
                problems.append(
                    f"{node.value}: {model_id} supports neither structured outputs nor tool "
                    f"calling, so no output tier is available ({env_var})"
                )
            if free_models_only and not caps.is_free:
                problems.append(
                    f"{node.value}: {model_id} is not free, and LLM_FREE_MODELS_ONLY is "
                    f"set. Choose a model whose every published price is zero ({env_var})."
                )
            if caps.prompt_usd_per_token < 0 or caps.completion_usd_per_token < 0:
                problems.append(
                    f"{node.value}: {model_id} has no usable pricing in the catalogue, so cost "
                    f"cannot be tracked ({env_var})"
                )

            resolved[node] = caps

        if problems:
            raise ModelCapabilityError(
                "The configured models cannot be used:\n  - " + "\n  - ".join(problems)
            )
        return resolved


async def fetch_catalogue(
    *, base_url: str, api_key: str | None, timeout_seconds: float
) -> ModelCatalogue:
    """Fetch the live catalogue from an OpenAI-compatible endpoint."""
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    url = f"{base_url.rstrip('/')}/models"
    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        response = await client.get(url, headers=headers)
        response.raise_for_status()
        payload: dict[str, Any] = response.json()
    return ModelCatalogue.from_provider_payload(payload)


async def load_catalogue(
    *, base_url: str, api_key: str | None, timeout_seconds: float, offline: bool
) -> tuple[ModelCatalogue, str]:
    """Load the catalogue, returning it with the source used.

    Falls back to the committed snapshot when the provider is unreachable, so
    a transient network failure degrades model *selection breadth* rather than
    preventing startup. The source is reported by ``/api/meta`` so nobody has
    to guess which one is in play.
    """
    if offline:
        return ModelCatalogue.from_snapshot(), "snapshot"
    try:
        catalogue = await fetch_catalogue(
            base_url=base_url, api_key=api_key, timeout_seconds=timeout_seconds
        )
    except (httpx.HTTPError, ValueError) as exc:
        log.warning(
            "catalogue_fetch_failed_using_snapshot",
            base_url=base_url,
            error=type(exc).__name__,
        )
        return ModelCatalogue.from_snapshot(), "snapshot_fallback"
    else:
        log.info("catalogue_loaded", base_url=base_url, models=len(catalogue))
        return catalogue, "live"
