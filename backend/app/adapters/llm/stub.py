"""Deterministic offline adapter.

The default for tests, CI and offline development. It makes no network call,
needs no credentials, and returns the same output for the same input every
time, so the whole workflow can be exercised without a provider.

Honesty rule: stub output is labelled as such everywhere it surfaces — the
task records ``llm_provider=stub`` and the UI shows it. It is never presented
as a live model response.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from app.adapters.llm.base import CallUsage, LLMResult
from app.domain.enums import AttemptOutcome, LLMProvider, OutputMode
from app.domain.errors import ConfigurationError, SchemaValidationError
from app.domain.nodes import NodeName

RESPONSES_PATH = Path(__file__).with_name("stub_responses.json")

#: Rough characters-per-token, used only to make the stub's usage numbers
#: plausible and repeatable. Never presented as a real token count.
_CHARS_PER_TOKEN = 4


def load_stub_responses(path: Path | None = None) -> dict[NodeName, Any]:
    """Load canned per-node responses from the committed fixture file."""
    target = path or RESPONSES_PATH
    if not target.exists():
        return {}
    with target.open(encoding="utf-8") as handle:
        raw: Mapping[str, Any] = json.load(handle)
    return {NodeName(node): body for node, body in raw.items()}


class StubLLMClient:
    """An :class:`~app.adapters.llm.base.LLMClient` backed by fixtures."""

    def __init__(self, responses: Mapping[NodeName, Any] | None = None) -> None:
        self._responses: dict[NodeName, Any] = (
            dict(responses) if responses is not None else load_stub_responses()
        )

    @property
    def provider(self) -> LLMProvider:
        return LLMProvider.STUB

    def register(self, node: NodeName, payload: Any) -> None:
        """Add or replace a canned response. Used by tests and fixtures."""
        self._responses[node] = payload

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
        payload = self._responses.get(node)
        if payload is None:
            raise ConfigurationError(
                f"No stub response registered for node {node.value!r}. Add one to "
                f"{RESPONSES_PATH.name} so the offline demo stays complete.",
                node=node.value,
            )

        try:
            output = schema.model_validate(payload)
        except ValidationError as exc:
            # A drifted fixture is a developer error, surfaced the same way a
            # real model's schema violation would be.
            raise SchemaValidationError(
                f"The stub response for {node.value!r} does not satisfy "
                f"{schema.__name__}: {exc.error_count()} validation error(s).",
                node=node.value,
                schema=schema.__name__,
            ) from exc

        prompt_chars = len(system) + len(user)
        tokens_in = max(prompt_chars // _CHARS_PER_TOKEN, 1)
        tokens_out = max(len(json.dumps(payload, default=str)) // _CHARS_PER_TOKEN, 1)
        # Derived from the prompt so repeated runs produce identical ledgers.
        digest = hashlib.sha256(f"{node.value}:{system}:{user}".encode()).hexdigest()
        latency_ms = 50 + int(digest[:4], 16) % 200

        return LLMResult(
            output=output,
            usage=CallUsage(
                node=node,
                provider=LLMProvider.STUB,
                model_requested="stub/deterministic",
                model_served="stub/deterministic",
                output_mode=OutputMode.JSON_SCHEMA,
                attempt=attempt,
                attempt_outcome=AttemptOutcome.OK,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                cache_read_tokens=0,
                latency_ms=latency_ms,
                generation_id=f"stub-{digest[:16]}",
                # No cost: no provider call was made. Zero is the truthful
                # value here, and distinguishes stub rows in the ledger.
                cost_usd_estimate=0.0,
                cost_usd_reported=0.0,
            ),
        )

    async def aclose(self) -> None:
        return None
