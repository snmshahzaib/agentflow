"""Catalogue parsing, capability validation and cost estimation.

Capability is not uniform across a multi-vendor catalogue, so these tests pin
down the behaviour the design depends on: a misconfiguration must be caught at
startup, must name the environment variable at fault, and must list every
problem at once rather than one per restart.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.adapters.llm.base import ModelCapabilities
from app.adapters.llm.capabilities import ModelCatalogue
from app.adapters.llm.pricing import estimate_cost_usd, estimate_task_cost_usd
from app.core.config import MIN_CONTEXT_TOKENS
from app.domain.enums import OutputMode
from app.domain.errors import ModelCapabilityError, ModelNotAvailableError
from app.domain.nodes import LLM_NODES, NodeName


def entry(
    *,
    context: int = 1_000_000,
    structured: bool = True,
    tools: bool = True,
    prompt: str = "0.000002",
    completion: str = "0.00001",
) -> dict[str, Any]:
    params = []
    if structured:
        params.append("structured_outputs")
    if tools:
        params += ["tools", "tool_choice"]
    return {
        "context_length": context,
        "supported_parameters": params,
        "pricing": {"prompt": prompt, "completion": completion},
    }


def catalogue(models: dict[str, dict[str, Any]]) -> ModelCatalogue:
    return ModelCatalogue.from_entries(models)


def all_nodes(model: str) -> dict[NodeName, str]:
    return dict.fromkeys(sorted(LLM_NODES), model)


class TestSnapshot:
    def test_the_committed_snapshot_loads(self) -> None:
        """CI and offline development depend on this file alone."""
        snap = ModelCatalogue.from_snapshot()
        assert len(snap) >= 4

    def test_every_snapshot_model_is_usable_for_a_node(self) -> None:
        """A snapshot entry that could not be configured would be dead weight."""
        snap = ModelCatalogue.from_snapshot()
        for model_id in ("anthropic/claude-sonnet-5", "anthropic/claude-haiku-5.5"):
            caps = snap.require(model_id)
            assert caps.supports_structured_outputs
            assert caps.context_length >= MIN_CONTEXT_TOKENS
            assert caps.prompt_usd_per_token > 0

    def test_documented_default_prices_match_the_snapshot(self) -> None:
        """docs/model-configuration.md quotes these; they must not drift."""
        snap = ModelCatalogue.from_snapshot()
        sonnet = snap.require("anthropic/claude-sonnet-5")
        haiku = snap.require("anthropic/claude-haiku-5.5")
        assert sonnet.prompt_usd_per_million == pytest.approx(2.0)
        assert sonnet.completion_usd_per_million == pytest.approx(10.0)
        assert haiku.prompt_usd_per_million == pytest.approx(0.10)
        assert haiku.completion_usd_per_million == pytest.approx(0.50)


class TestProviderPayloadParsing:
    def test_an_openai_compatible_models_response_is_parsed(self) -> None:
        payload = {
            "data": [
                {"id": "vendor/model-a", **entry()},
                {"id": "vendor/model-b", **entry(structured=False)},
                {"not_an_id": True},
            ]
        }
        cat = ModelCatalogue.from_provider_payload(payload)
        assert len(cat) == 2
        assert cat.require("vendor/model-a").supports_structured_outputs
        assert not cat.require("vendor/model-b").supports_structured_outputs

    def test_response_format_alone_counts_as_structured_support(self) -> None:
        cat = ModelCatalogue.from_entries(
            {"vendor/model-a": {"supported_parameters": ["response_format"], "pricing": {}}}
        )
        assert cat.require("vendor/model-a").supports_structured_outputs

    def test_a_negative_price_is_treated_as_unknown(self) -> None:
        """Some catalogues mark dynamic routing entries with a negative price;
        such an entry must never be selected for a node."""
        cat = catalogue({"vendor/auto": entry(prompt="-1", completion="-1")})
        caps = cat.require("vendor/auto")
        assert caps.prompt_usd_per_token == -1.0
        with pytest.raises(ModelCapabilityError, match="no usable pricing"):
            cat.validate_node_models(
                all_nodes("vendor/auto"),
                require_structured_output=True,
                min_context_tokens=MIN_CONTEXT_TOKENS,
            )

    def test_a_missing_model_reports_how_to_fix_it(self) -> None:
        cat = catalogue({"vendor/model-a": entry()})
        with pytest.raises(ModelNotAvailableError) as excinfo:
            cat.require("vendor/retired")
        assert "vendor/retired" in str(excinfo.value)
        assert "docs/model-configuration.md" in str(excinfo.value)


class TestNodeValidation:
    def test_a_valid_configuration_resolves_every_node(self) -> None:
        cat = catalogue({"vendor/model-a": entry()})
        resolved = cat.validate_node_models(
            all_nodes("vendor/model-a"),
            require_structured_output=True,
            min_context_tokens=MIN_CONTEXT_TOKENS,
        )
        assert set(resolved) == set(LLM_NODES)

    def test_an_unknown_model_names_the_environment_variable(self) -> None:
        cat = catalogue({"vendor/model-a": entry()})
        with pytest.raises(ModelCapabilityError) as excinfo:
            cat.validate_node_models(
                {NodeName.DRAFT: "vendor/typo"},
                require_structured_output=True,
                min_context_tokens=MIN_CONTEXT_TOKENS,
            )
        assert "LLM_MODEL_DRAFT" in str(excinfo.value)

    def test_the_planner_variable_is_named_correctly(self) -> None:
        """The planner's env var is LLM_MODEL_PLANNER, not LLM_MODEL_PLAN."""
        cat = catalogue({"vendor/model-a": entry()})
        with pytest.raises(ModelCapabilityError) as excinfo:
            cat.validate_node_models(
                {NodeName.PLAN: "vendor/typo"},
                require_structured_output=True,
                min_context_tokens=MIN_CONTEXT_TOKENS,
            )
        assert "LLM_MODEL_PLANNER" in str(excinfo.value)

    def test_a_small_context_window_is_refused(self) -> None:
        cat = catalogue({"vendor/small": entry(context=8_192)})
        with pytest.raises(ModelCapabilityError, match="context window"):
            cat.validate_node_models(
                all_nodes("vendor/small"),
                require_structured_output=True,
                min_context_tokens=MIN_CONTEXT_TOKENS,
            )

    def test_no_structured_output_is_refused_in_strict_mode(self) -> None:
        """The refusal must explain that this is a security boundary."""
        cat = catalogue({"vendor/tools-only": entry(structured=False)})
        with pytest.raises(ModelCapabilityError) as excinfo:
            cat.validate_node_models(
                all_nodes("vendor/tools-only"),
                require_structured_output=True,
                min_context_tokens=MIN_CONTEXT_TOKENS,
            )
        message = str(excinfo.value)
        assert "structured outputs" in message
        assert "prompt-injection boundary" in message

    def test_a_tool_capable_model_is_accepted_when_strictness_is_relaxed(self) -> None:
        cat = catalogue({"vendor/tools-only": entry(structured=False)})
        resolved = cat.validate_node_models(
            all_nodes("vendor/tools-only"),
            require_structured_output=False,
            min_context_tokens=MIN_CONTEXT_TOKENS,
        )
        assert resolved[NodeName.DRAFT].supports_tools

    def test_a_model_with_no_output_tier_is_always_refused(self) -> None:
        cat = catalogue({"vendor/plain": entry(structured=False, tools=False)})
        with pytest.raises(ModelCapabilityError, match="neither structured outputs nor tool"):
            cat.validate_node_models(
                all_nodes("vendor/plain"),
                require_structured_output=False,
                min_context_tokens=MIN_CONTEXT_TOKENS,
            )

    def test_an_empty_model_id_is_refused(self) -> None:
        cat = catalogue({"vendor/model-a": entry()})
        with pytest.raises(ModelCapabilityError, match="no model configured"):
            cat.validate_node_models(
                {NodeName.ANALYSE: ""},
                require_structured_output=True,
                min_context_tokens=MIN_CONTEXT_TOKENS,
            )

    def test_all_problems_are_reported_at_once(self) -> None:
        """A developer fixing configuration wants the full list, not one
        error per restart."""
        cat = catalogue({"vendor/small": entry(context=1_000)})
        with pytest.raises(ModelCapabilityError) as excinfo:
            cat.validate_node_models(
                {
                    NodeName.PLAN: "vendor/missing",
                    NodeName.ANALYSE: "vendor/small",
                    NodeName.DRAFT: "",
                },
                require_structured_output=True,
                min_context_tokens=MIN_CONTEXT_TOKENS,
            )
        message = str(excinfo.value)
        assert message.count("- ") >= 3
        for fragment in ("LLM_MODEL_PLANNER", "LLM_MODEL_ANALYST", "LLM_MODEL_DRAFT"):
            assert fragment in message


class TestOutputTierSelection:
    def test_native_schema_support_wins(self) -> None:
        caps = ModelCapabilities("m", 1_000_000, True, True, 1e-6, 1e-6)
        assert caps.output_mode(require_structured_output=True) is OutputMode.JSON_SCHEMA

    def test_tool_calling_is_the_second_tier(self) -> None:
        caps = ModelCapabilities("m", 1_000_000, False, True, 1e-6, 1e-6)
        assert caps.output_mode(require_structured_output=False) is OutputMode.TOOL_CALL

    def test_prompt_json_is_only_reachable_with_strictness_relaxed(self) -> None:
        caps = ModelCapabilities("m", 1_000_000, False, False, 1e-6, 1e-6)
        assert caps.output_mode(require_structured_output=False) is OutputMode.PROMPT_JSON
        with pytest.raises(ValueError, match="neither structured outputs nor tool calling"):
            caps.output_mode(require_structured_output=True)


class TestCostEstimation:
    def test_cost_is_computed_from_catalogue_pricing(self) -> None:
        caps = ModelCapabilities("m", 1_000_000, True, True, 2e-6, 1e-5)
        # 45k prompt tokens at $2/M + 15k completion at $10/M
        assert estimate_cost_usd(caps, tokens_in=45_000, tokens_out=15_000) == pytest.approx(0.24)

    def test_unknown_pricing_yields_no_estimate_rather_than_zero(self) -> None:
        """Reporting $0 for an unpriced model would understate spend."""
        caps = ModelCapabilities("m", 1_000_000, True, True, -1.0, -1.0)
        assert estimate_cost_usd(caps, tokens_in=1000, tokens_out=1000) is None

    def test_cached_tokens_are_counted_at_the_prompt_rate(self) -> None:
        """Erring high is the safe direction for a budget ceiling."""
        caps = ModelCapabilities("m", 1_000_000, True, True, 1e-6, 1e-6)
        with_cache = estimate_cost_usd(caps, tokens_in=1_000, tokens_out=0, cache_read_tokens=1_000)
        assert with_cache == pytest.approx(0.002)

    def test_negative_token_counts_cannot_reduce_the_estimate(self) -> None:
        caps = ModelCapabilities("m", 1_000_000, True, True, 1e-6, 1e-6)
        assert estimate_cost_usd(caps, tokens_in=-5_000, tokens_out=1_000) == pytest.approx(0.001)

    def test_a_task_projection_scales_by_call_count(self) -> None:
        caps = ModelCapabilities("m", 1_000_000, True, True, 2e-6, 1e-5)
        assert estimate_task_cost_usd(
            caps, tokens_in=10_000, tokens_out=2_000, calls=4
        ) == pytest.approx((10_000 * 2e-6 + 2_000 * 1e-5) * 4)
