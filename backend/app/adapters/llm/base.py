"""The LLM port.

AgentFlow is coupled to this interface, never to a vendor. Node code calls
:meth:`LLMClient.complete_structured` with a Pydantic schema and receives a
validated object plus a usage record; which provider answered is
configuration.

Nothing in this module imports a vendor SDK — see ``openai_compat`` for the
only place that does.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, TypeVar

from pydantic import BaseModel

from app.domain.enums import AttemptOutcome, LLMProvider, OutputMode
from app.domain.errors import ModelCapabilityError
from app.domain.nodes import NodeName

T = TypeVar("T", bound=BaseModel)


@dataclass(frozen=True, slots=True)
class ModelCapabilities:
    """What a provider's catalogue says one model can do, and what it costs.

    Prices are per **token**, as catalogues publish them; multiply before
    displaying anything per million.
    """

    model_id: str
    context_length: int
    supports_structured_outputs: bool
    supports_tools: bool
    prompt_usd_per_token: float
    completion_usd_per_token: float
    #: True only when *every* pricing field the catalogue publishes is zero.
    #: A non-zero request or image fee makes a nominally "free" model
    #: billable, so this is not derived from the token prices alone.
    is_free: bool = False

    @property
    def prompt_usd_per_million(self) -> float:
        return self.prompt_usd_per_token * 1_000_000

    @property
    def completion_usd_per_million(self) -> float:
        return self.completion_usd_per_token * 1_000_000

    def output_mode(self, *, require_structured_output: bool) -> OutputMode:
        """Pick the output tier for this model, refusing to downgrade quietly.

        Tier order is a security decision, not a preference: schema-constrained
        output is layer 3 of the prompt-injection boundary (docs/security.md).

        Under ``require_structured_output`` **only native JSON-Schema is
        acceptable**. Returning a weaker tier here would be exactly the silent
        downgrade the setting exists to prevent -- the caller asked for a
        guarantee, so an unmet guarantee has to be an error, not a quieter
        promise.
        """
        if self.supports_structured_outputs:
            return OutputMode.JSON_SCHEMA

        if require_structured_output:
            raise ModelCapabilityError(
                f"{self.model_id} does not support native structured outputs and "
                "LLM_REQUIRE_STRUCTURED_OUTPUT is set. Choose a model that does, rather "
                "than relaxing the requirement: schema-constrained output is part of the "
                "prompt-injection boundary (docs/security.md).",
                model=self.model_id,
            )

        if self.supports_tools:
            return OutputMode.TOOL_CALL
        return OutputMode.PROMPT_JSON


@dataclass(frozen=True, slots=True)
class CallUsage:
    """One row of the cost ledger.

    ``model_requested`` and ``model_served`` differ whenever the fallback
    chain fires, which is exactly when the audit trail matters most.
    """

    node: NodeName
    provider: LLMProvider
    model_requested: str
    model_served: str | None
    output_mode: OutputMode
    attempt: int
    attempt_outcome: AttemptOutcome
    tokens_in: int
    tokens_out: int
    cache_read_tokens: int
    latency_ms: int
    generation_id: str | None = None
    #: The company that actually served the request, as reported by the
    #: gateway. Distinct from ``model_served``: two different model ids can
    #: be hosted by the same provider, so vendor diversity cannot be inferred
    #: from model ids alone.
    provider_served: str | None = None
    cost_usd_estimate: float | None = None
    cost_usd_reported: float | None = None

    @property
    def total_tokens(self) -> int:
        return self.tokens_in + self.tokens_out


@dataclass(frozen=True, slots=True)
class LLMResult[TOut: BaseModel]:
    """A validated model output together with what it cost to obtain."""

    output: TOut
    usage: CallUsage


class LLMClient(Protocol):
    """The only interface node code may use to reach a language model."""

    @property
    def provider(self) -> LLMProvider: ...

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
        """Return a schema-valid object, or raise.

        Implementations must never return unvalidated model text, and must
        never expose tools to the model: retrieved content is untrusted, and
        nothing the model emits may reach the network.
        """
        ...

    async def aclose(self) -> None: ...
