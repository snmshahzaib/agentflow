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

    @property
    def prompt_usd_per_million(self) -> float:
        return self.prompt_usd_per_token * 1_000_000

    @property
    def completion_usd_per_million(self) -> float:
        return self.completion_usd_per_token * 1_000_000

    def output_mode(self, *, require_structured_output: bool) -> OutputMode:
        """Pick the strongest output tier this model supports.

        Tier order is a security decision, not a preference: schema-constrained
        output is layer 3 of the prompt-injection boundary (docs/security.md).
        """
        if self.supports_structured_outputs:
            return OutputMode.JSON_SCHEMA
        if self.supports_tools:
            return OutputMode.TOOL_CALL
        if require_structured_output:
            # Caller must decide; validation refuses this model up front.
            raise ValueError(
                f"{self.model_id} supports neither structured outputs nor tool calling"
            )
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
