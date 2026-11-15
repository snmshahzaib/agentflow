"""Stub adapter, error normalisation and the OpenAI-compatible gateway.

The gateway tests drive the real adapter logic — tier selection, payload
extraction, usage accounting, retries and the cross-vendor fallback chain —
against a fake transport, so no network or credentials are involved.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from pydantic import BaseModel

from app.adapters.llm.base import ModelCapabilities
from app.adapters.llm.capabilities import ModelCatalogue
from app.adapters.llm.errors import normalise_exception, normalise_http_status
from app.adapters.llm.openai_compat import OpenAICompatibleLLMClient, strictify_schema
from app.adapters.llm.stub import StubLLMClient
from app.domain.enums import AttemptOutcome, LLMProvider, OutputMode
from app.domain.errors import (
    AdapterError,
    ConfigurationError,
    ProviderAuthError,
    ProviderRateLimitedError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    SchemaValidationError,
)
from app.domain.nodes import NodeName


class Plan(BaseModel):
    """A stand-in for a real node schema."""

    subtasks: list[str]
    rationale: str = "default rationale"


# ---------------------------------------------------------------------------
# Stub adapter
# ---------------------------------------------------------------------------


class TestStubAdapter:
    async def test_a_registered_response_is_validated_and_returned(self) -> None:
        client = StubLLMClient({NodeName.PLAN: {"subtasks": ["a", "b"], "rationale": "r"}})
        result = await client.complete_structured(
            node=NodeName.PLAN, schema=Plan, system="sys", user="usr"
        )
        assert result.output.subtasks == ["a", "b"]
        assert result.usage.provider is LLMProvider.STUB

    async def test_an_unregistered_node_fails_loudly(self) -> None:
        """Silently returning nothing would let an incomplete offline demo
        look complete."""
        client = StubLLMClient({})
        with pytest.raises(ConfigurationError, match="No stub response registered"):
            await client.complete_structured(node=NodeName.DRAFT, schema=Plan, system="s", user="u")

    async def test_a_drifted_fixture_is_reported_as_a_schema_failure(self) -> None:
        client = StubLLMClient({NodeName.PLAN: {"wrong_field": 1}})
        with pytest.raises(SchemaValidationError, match="does not satisfy Plan"):
            await client.complete_structured(node=NodeName.PLAN, schema=Plan, system="s", user="u")

    async def test_output_is_deterministic_for_identical_input(self) -> None:
        """The offline demo and CI depend on repeatability."""
        client = StubLLMClient({NodeName.PLAN: {"subtasks": ["a"]}})
        first = await client.complete_structured(
            node=NodeName.PLAN, schema=Plan, system="s", user="u"
        )
        second = await client.complete_structured(
            node=NodeName.PLAN, schema=Plan, system="s", user="u"
        )
        assert first.usage == second.usage

    async def test_a_different_prompt_produces_different_usage(self) -> None:
        client = StubLLMClient({NodeName.PLAN: {"subtasks": ["a"]}})
        a = await client.complete_structured(node=NodeName.PLAN, schema=Plan, system="s", user="u")
        b = await client.complete_structured(
            node=NodeName.PLAN, schema=Plan, system="s", user="different"
        )
        assert a.usage.latency_ms != b.usage.latency_ms or a.usage.tokens_in != b.usage.tokens_in

    async def test_stub_calls_cost_nothing_and_say_so(self) -> None:
        """No provider call was made, so zero is the truthful figure and it
        distinguishes stub rows in the ledger."""
        client = StubLLMClient({NodeName.PLAN: {"subtasks": ["a"]}})
        usage = (
            await client.complete_structured(node=NodeName.PLAN, schema=Plan, system="s", user="u")
        ).usage
        assert usage.cost_usd_estimate == 0.0
        assert usage.model_served == "stub/deterministic"


# ---------------------------------------------------------------------------
# Error normalisation
# ---------------------------------------------------------------------------


class TestErrorNormalisation:
    @pytest.mark.parametrize(
        ("status", "expected", "retryable"),
        [
            (401, ProviderAuthError, False),
            (403, ProviderAuthError, False),
            (429, ProviderRateLimitedError, True),
            (408, ProviderTimeoutError, True),
            (500, ProviderUnavailableError, True),
            (503, ProviderUnavailableError, True),
            (524, ProviderUnavailableError, True),
            (400, AdapterError, False),
            (404, AdapterError, False),
        ],
    )
    def test_status_codes_map_to_domain_errors(
        self, status: int, expected: type[AdapterError], retryable: bool
    ) -> None:
        error = normalise_http_status(status)
        assert isinstance(error, expected)
        assert error.retryable is retryable

    def test_a_transport_timeout_is_retryable(self) -> None:
        error = normalise_exception(httpx.ReadTimeout("timed out"))
        assert isinstance(error, ProviderTimeoutError)
        assert error.retryable

    def test_a_connection_failure_is_retryable(self) -> None:
        assert normalise_exception(httpx.ConnectError("refused")).retryable

    def test_an_sdk_exception_is_mapped_by_its_status_code(self) -> None:
        """Matching is structural, so this module needs no vendor import."""

        class FakeSdkError(Exception):
            status_code = 429

        assert isinstance(normalise_exception(FakeSdkError()), ProviderRateLimitedError)

    def test_an_unrecognised_failure_is_retryable_once(self) -> None:
        """A transient provider quirk should not fail a whole research task;
        the per-task attempt cap bounds the cost of being wrong."""
        error = normalise_exception(RuntimeError("something odd"))
        assert error.retryable

    def test_a_domain_error_passes_through_unchanged(self) -> None:
        original = ProviderAuthError("bad key")
        assert normalise_exception(original) is original


# ---------------------------------------------------------------------------
# Schema strictification
# ---------------------------------------------------------------------------


class TestStrictSchema:
    def test_objects_forbid_extra_properties_and_require_every_field(self) -> None:
        """Strict structured outputs reject a schema that does not."""
        strict = strictify_schema(Plan.model_json_schema())
        assert strict["additionalProperties"] is False
        assert set(strict["required"]) == {"subtasks", "rationale"}

    def test_nested_definitions_are_tightened_too(self) -> None:
        class Inner(BaseModel):
            value: str = "x"

        class Outer(BaseModel):
            inner: Inner
            items: list[Inner] = []

        strict = strictify_schema(Outer.model_json_schema())
        assert strict["$defs"]["Inner"]["additionalProperties"] is False
        assert strict["$defs"]["Inner"]["required"] == ["value"]

    def test_a_non_object_schema_is_returned_unchanged(self) -> None:
        assert strictify_schema({"type": "string"}) == {"type": "string"}


# ---------------------------------------------------------------------------
# Gateway adapter, driven against a fake transport
# ---------------------------------------------------------------------------


class FakeMessage:
    def __init__(self, content: str | None = None, tool_calls: Any = None) -> None:
        self.content = content
        self.tool_calls = tool_calls


class FakeUsage:
    def __init__(self, tokens_in: int, tokens_out: int, cost: float | None = None) -> None:
        self.prompt_tokens = tokens_in
        self.completion_tokens = tokens_out
        self.prompt_tokens_details = None
        if cost is not None:
            self.cost = cost


class FakeResponse:
    def __init__(
        self,
        content: str | None = None,
        *,
        model: str = "vendor-a/primary",
        tokens: tuple[int, int] = (1000, 500),
        cost: float | None = None,
        tool_calls: Any = None,
    ) -> None:
        self.choices = [type("C", (), {"message": FakeMessage(content, tool_calls)})()]
        self.model = model
        self.id = "gen-123"
        self.usage = FakeUsage(*tokens, cost=cost)


class FakeCompletions:
    """Records calls and replays a scripted sequence of results."""

    def __init__(self, script: list[Any]) -> None:
        self.script = list(script)
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        outcome = self.script.pop(0) if self.script else self.script
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def build_client(script: list[Any], **overrides: Any) -> tuple[Any, FakeCompletions]:
    catalogue = ModelCatalogue.from_entries(
        {
            "vendor-a/primary": {
                "context_length": 1_000_000,
                "supported_parameters": ["structured_outputs", "tools"],
                "pricing": {"prompt": "0.000002", "completion": "0.00001"},
            },
            "vendor-b/fallback": {
                "context_length": 500_000,
                "supported_parameters": ["structured_outputs", "tools"],
                "pricing": {"prompt": "0.000001", "completion": "0.000005"},
            },
            "vendor-c/tools-only": {
                "context_length": 400_000,
                "supported_parameters": ["tools"],
                "pricing": {"prompt": "0.000001", "completion": "0.000005"},
            },
        }
    )
    kwargs: dict[str, Any] = {
        "catalogue": catalogue,
        "models": dict.fromkeys(NodeName, "vendor-a/primary"),
        "base_url": "https://gateway.test/api/v1",
        "api_key": "unit-test-credential-value",
        "fallback_models": ["vendor-b/fallback"],
        "max_attempts": 2,
    }
    kwargs.update(overrides)
    client = OpenAICompatibleLLMClient(**kwargs)
    fake = FakeCompletions(script)
    client._client = type("C", (), {"chat": type("Ch", (), {"completions": fake})()})()
    return client, fake


class TestGatewaySuccessPath:
    async def test_a_schema_valid_response_is_parsed(self) -> None:
        client, _ = build_client([FakeResponse('{"subtasks":["a","b"],"rationale":"r"}')])
        result = await client.complete_structured(
            node=NodeName.PLAN, schema=Plan, system="s", user="u"
        )
        assert result.output.subtasks == ["a", "b"]
        assert result.usage.output_mode is OutputMode.JSON_SCHEMA
        assert result.usage.attempt_outcome is AttemptOutcome.OK

    async def test_a_strict_json_schema_is_sent_for_a_tier_one_model(self) -> None:
        client, fake = build_client([FakeResponse('{"subtasks":[],"rationale":"r"}')])
        await client.complete_structured(node=NodeName.PLAN, schema=Plan, system="s", user="u")
        fmt = fake.calls[0]["response_format"]
        assert fmt["type"] == "json_schema"
        assert fmt["json_schema"]["strict"] is True
        assert fmt["json_schema"]["schema"]["additionalProperties"] is False

    async def test_no_tools_are_offered_on_the_schema_tier(self) -> None:
        """Retrieved content is untrusted; the model gets no capabilities."""
        client, fake = build_client([FakeResponse('{"subtasks":[],"rationale":"r"}')])
        await client.complete_structured(node=NodeName.PLAN, schema=Plan, system="s", user="u")
        assert "tools" not in fake.calls[0]

    async def test_routing_preferences_pin_provider_behaviour(self) -> None:
        """A model that advertises a capability is not the same as a provider
        that honours it."""
        client, fake = build_client([FakeResponse('{"subtasks":[],"rationale":"r"}')])
        await client.complete_structured(node=NodeName.PLAN, schema=Plan, system="s", user="u")
        provider = fake.calls[0]["extra_body"]["provider"]
        assert provider["require_parameters"] is True
        assert provider["data_collection"] == "deny"
        assert fake.calls[0]["extra_body"]["usage"]["include"] is True

    async def test_usage_records_tokens_cost_and_the_model_served(self) -> None:
        client, _ = build_client(
            [
                FakeResponse(
                    '{"subtasks":[],"rationale":"r"}',
                    model="vendor-a/primary-20261101",
                    tokens=(45_000, 15_000),
                    cost=0.2371,
                )
            ]
        )
        usage = (
            await client.complete_structured(node=NodeName.PLAN, schema=Plan, system="s", user="u")
        ).usage
        assert usage.tokens_in == 45_000
        assert usage.tokens_out == 15_000
        # Catalogue pricing: 45k at $2/M + 15k at $10/M
        assert usage.cost_usd_estimate == pytest.approx(0.24)
        assert usage.cost_usd_reported == pytest.approx(0.2371)
        assert usage.model_requested == "vendor-a/primary"
        assert usage.model_served == "vendor-a/primary-20261101"
        assert usage.generation_id == "gen-123"

    async def test_a_code_fenced_response_is_still_parsed(self) -> None:
        """Some models wrap JSON in a fence even under a schema instruction."""
        client, _ = build_client([FakeResponse('```json\n{"subtasks":["a"],"rationale":"r"}\n```')])
        result = await client.complete_structured(
            node=NodeName.PLAN, schema=Plan, system="s", user="u"
        )
        assert result.output.subtasks == ["a"]


class TestGatewayTierTwo:
    async def test_a_tool_only_model_uses_a_forced_function_as_schema_carrier(self) -> None:
        tool_call = type(
            "TC",
            (),
            {"function": type("F", (), {"arguments": '{"subtasks":["x"],"rationale":"r"}'})()},
        )()
        client, fake = build_client(
            [FakeResponse(tool_calls=[tool_call])],
            models=dict.fromkeys(NodeName, "vendor-c/tools-only"),
            require_structured_output=False,
            fallback_models=[],
        )
        result = await client.complete_structured(
            node=NodeName.PLAN, schema=Plan, system="s", user="u"
        )
        assert result.output.subtasks == ["x"]
        assert result.usage.output_mode is OutputMode.TOOL_CALL
        assert fake.calls[0]["tool_choice"]["function"]["name"] == "Plan"

    async def test_a_missing_tool_call_is_a_schema_failure(self) -> None:
        client, _ = build_client(
            [FakeResponse(content="I would rather explain in prose.")] * 2,
            models=dict.fromkeys(NodeName, "vendor-c/tools-only"),
            require_structured_output=False,
            fallback_models=[],
        )
        with pytest.raises(SchemaValidationError, match="no tool call"):
            await client.complete_structured(node=NodeName.PLAN, schema=Plan, system="s", user="u")


class TestGatewayResilience:
    async def test_a_retryable_failure_is_retried_on_the_same_model(self) -> None:
        client, fake = build_client(
            [normalise_http_status(503), FakeResponse('{"subtasks":["a"],"rationale":"r"}')]
        )
        result = await client.complete_structured(
            node=NodeName.PLAN, schema=Plan, system="s", user="u"
        )
        assert result.usage.attempt_outcome is AttemptOutcome.RETRIED
        assert [c["model"] for c in fake.calls] == ["vendor-a/primary"] * 2

    async def test_exhausting_the_primary_falls_back_to_another_vendor(self) -> None:
        """The point of a gateway: one provider's outage degrades model
        quality instead of failing the research task."""
        client, fake = build_client(
            [
                normalise_http_status(503),
                normalise_http_status(503),
                FakeResponse('{"subtasks":["a"],"rationale":"r"}', model="vendor-b/fallback"),
            ]
        )
        result = await client.complete_structured(
            node=NodeName.PLAN, schema=Plan, system="s", user="u"
        )
        assert result.usage.attempt_outcome is AttemptOutcome.FELL_BACK
        assert result.usage.model_served == "vendor-b/fallback"
        assert [c["model"] for c in fake.calls] == [
            "vendor-a/primary",
            "vendor-a/primary",
            "vendor-b/fallback",
        ]

    async def test_a_non_retryable_failure_skips_retries_but_tries_the_fallback(self) -> None:
        """Retrying a 400 wastes time; a different model may still accept it."""
        client, fake = build_client(
            [
                normalise_http_status(400),
                FakeResponse('{"subtasks":["a"],"rationale":"r"}', model="vendor-b/fallback"),
            ]
        )
        result = await client.complete_structured(
            node=NodeName.PLAN, schema=Plan, system="s", user="u"
        )
        assert [c["model"] for c in fake.calls] == ["vendor-a/primary", "vendor-b/fallback"]
        assert result.usage.attempt_outcome is AttemptOutcome.FELL_BACK

    async def test_an_auth_failure_is_not_retried_on_the_same_model(self) -> None:
        client, fake = build_client([normalise_http_status(401)] * 2, fallback_models=[])
        with pytest.raises(ProviderAuthError):
            await client.complete_structured(node=NodeName.PLAN, schema=Plan, system="s", user="u")
        assert len(fake.calls) == 1

    async def test_when_every_model_fails_the_last_error_surfaces(self) -> None:
        client, _ = build_client([normalise_http_status(503)] * 4)
        with pytest.raises(ProviderUnavailableError):
            await client.complete_structured(node=NodeName.PLAN, schema=Plan, system="s", user="u")

    async def test_invalid_json_is_treated_as_retryable(self) -> None:
        client, fake = build_client(
            [FakeResponse("not json at all"), FakeResponse('{"subtasks":["a"],"rationale":"r"}')]
        )
        result = await client.complete_structured(
            node=NodeName.PLAN, schema=Plan, system="s", user="u"
        )
        assert result.output.subtasks == ["a"]
        assert len(fake.calls) == 2

    async def test_the_fallback_chain_never_repeats_the_primary(self) -> None:
        client, fake = build_client(
            [normalise_http_status(503)] * 2 + [normalise_http_status(503)] * 2,
            fallback_models=["vendor-a/primary", "vendor-b/fallback"],
        )
        with pytest.raises(ProviderUnavailableError):
            await client.complete_structured(node=NodeName.PLAN, schema=Plan, system="s", user="u")
        assert [c["model"] for c in fake.calls] == [
            "vendor-a/primary",
            "vendor-a/primary",
            "vendor-b/fallback",
            "vendor-b/fallback",
        ]


class TestCapabilitiesHelper:
    def test_per_million_prices_are_derived_from_per_token(self) -> None:
        caps = ModelCapabilities("m", 1_000, True, True, 2e-6, 1e-5)
        assert caps.prompt_usd_per_million == pytest.approx(2.0)
        assert caps.completion_usd_per_million == pytest.approx(10.0)
