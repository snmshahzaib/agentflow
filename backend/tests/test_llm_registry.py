"""Provider selection and fail-fast startup validation.

These are the M1 acceptance criteria for the LLM spine: models resolve per
node from the environment, and an unusable configuration refuses to start
rather than failing part way through a user's research task.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.adapters.llm import registry
from app.adapters.llm.capabilities import ModelCatalogue
from app.adapters.llm.registry import STUB_MODEL_ID, build_llm_runtime
from app.domain.enums import LLMProvider
from app.domain.errors import ModelCapabilityError
from app.domain.nodes import LLM_NODES, NodeName
from tests.conftest import make_settings


def fake_catalogue() -> ModelCatalogue:
    priced = {"prompt": "0.000002", "completion": "0.00001"}
    return ModelCatalogue.from_entries(
        {
            "vendor-a/primary": {
                "context_length": 1_000_000,
                "supported_parameters": ["structured_outputs", "tools"],
                "pricing": priced,
            },
            "vendor-b/fallback": {
                "context_length": 400_000,
                "supported_parameters": ["structured_outputs", "tools"],
                "pricing": priced,
            },
            "vendor-c/tools-only": {
                "context_length": 400_000,
                "supported_parameters": ["tools"],
                "pricing": priced,
            },
            "vendor-d/small": {
                "context_length": 8_192,
                "supported_parameters": ["structured_outputs"],
                "pricing": priced,
            },
        }
    )


@pytest.fixture
def offline_catalogue(monkeypatch: pytest.MonkeyPatch) -> None:
    """Serve a fake catalogue instead of reaching the network."""

    async def _load(**_: Any) -> tuple[ModelCatalogue, str]:
        return fake_catalogue(), "live"

    monkeypatch.setattr(registry, "load_catalogue", _load)


class TestStubRuntime:
    async def test_the_default_configuration_needs_no_model_and_no_key(self) -> None:
        runtime = await build_llm_runtime(make_settings())
        assert runtime.provider is LLMProvider.STUB
        assert runtime.is_live is False
        assert runtime.catalogue_source == "stub"
        assert set(runtime.models) == set(LLM_NODES)
        assert set(runtime.models.values()) == {STUB_MODEL_ID}

    async def test_configured_models_are_validated_against_the_snapshot(self) -> None:
        """Catching a bad model ID while offline beats discovering it on the
        first live run."""
        runtime = await build_llm_runtime(
            make_settings(
                llm_model_default="anthropic/claude-sonnet-5",
                llm_model_planner="anthropic/claude-haiku-5.5",
            )
        )
        assert runtime.catalogue_source == "snapshot"
        assert runtime.capabilities[NodeName.PLAN].model_id == "anthropic/claude-haiku-5.5"
        assert runtime.capabilities[NodeName.DRAFT].model_id == "anthropic/claude-sonnet-5"

    async def test_a_bad_model_fails_even_in_offline_mode(self) -> None:
        with pytest.raises(ModelCapabilityError, match="LLM_MODEL_DRAFT"):
            await build_llm_runtime(
                make_settings(
                    llm_model_default="anthropic/claude-sonnet-5",
                    llm_model_draft="vendor/does-not-exist",
                )
            )


class TestGatewayRuntime:
    async def test_a_valid_live_configuration_builds_a_client(
        self, offline_catalogue: None
    ) -> None:
        runtime = await build_llm_runtime(
            make_settings(
                llm_provider="openrouter",
                llm_api_key="unit-test-credential-value",
                llm_model_default="vendor-a/primary",
                llm_fallback_models="vendor-b/fallback",
            )
        )
        assert runtime.is_live
        assert runtime.provider is LLMProvider.OPENROUTER
        assert runtime.catalogue_source == "live"
        assert runtime.fallback_models == ["vendor-b/fallback"]
        assert runtime.models[NodeName.DRAFT] == "vendor-a/primary"
        await runtime.client.aclose()

    async def test_per_node_overrides_reach_the_runtime(self, offline_catalogue: None) -> None:
        runtime = await build_llm_runtime(
            make_settings(
                llm_provider="openrouter",
                llm_api_key="unit-test-credential-value",
                llm_model_default="vendor-a/primary",
                llm_model_planner="vendor-b/fallback",
            )
        )
        assert runtime.models[NodeName.PLAN] == "vendor-b/fallback"
        assert runtime.models[NodeName.ANALYSE] == "vendor-a/primary"
        await runtime.client.aclose()

    async def test_an_unusable_primary_refuses_to_start(self, offline_catalogue: None) -> None:
        with pytest.raises(ModelCapabilityError, match="context window"):
            await build_llm_runtime(
                make_settings(
                    llm_provider="openrouter",
                    llm_api_key="unit-test-credential-value",
                    llm_model_default="vendor-d/small",
                )
            )

    async def test_strict_mode_refuses_a_tool_only_primary(self, offline_catalogue: None) -> None:
        with pytest.raises(ModelCapabilityError, match="prompt-injection boundary"):
            await build_llm_runtime(
                make_settings(
                    llm_provider="openrouter",
                    llm_api_key="unit-test-credential-value",
                    llm_model_default="vendor-c/tools-only",
                )
            )

    @pytest.mark.parametrize(
        "bad_fallback",
        ["vendor/unknown", "vendor-c/tools-only", "vendor-d/small"],
    )
    async def test_an_unusable_fallback_is_dropped_not_fatal(
        self, offline_catalogue: None, bad_fallback: str
    ) -> None:
        """A degraded-path model must not take down a working primary."""
        runtime = await build_llm_runtime(
            make_settings(
                llm_provider="openrouter",
                llm_api_key="unit-test-credential-value",
                llm_model_default="vendor-a/primary",
                llm_fallback_models=f"{bad_fallback},vendor-b/fallback",
            )
        )
        assert runtime.fallback_models == ["vendor-b/fallback"]
        await runtime.client.aclose()


class TestCatalogueFallback:
    async def test_an_unreachable_provider_degrades_to_the_snapshot(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A transient network failure should narrow model choice, not block
        startup — and the source is reported so nobody has to guess."""
        import httpx

        from app.adapters.llm import capabilities as caps_module

        async def _fail(**_: Any) -> ModelCatalogue:
            raise httpx.ConnectError("gateway unreachable")

        monkeypatch.setattr(caps_module, "fetch_catalogue", _fail)
        catalogue, source = await caps_module.load_catalogue(
            base_url="https://gateway.test/api/v1",
            api_key="unit-test-credential-value",
            timeout_seconds=1.0,
            offline=False,
        )
        assert source == "snapshot_fallback"
        assert "anthropic/claude-sonnet-5" in catalogue

    async def test_offline_mode_never_attempts_a_fetch(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.adapters.llm import capabilities as caps_module

        async def _explode(**_: Any) -> ModelCatalogue:
            raise AssertionError("offline mode must not touch the network")

        monkeypatch.setattr(caps_module, "fetch_catalogue", _explode)
        _, source = await caps_module.load_catalogue(
            base_url="https://gateway.test/api/v1",
            api_key=None,
            timeout_seconds=1.0,
            offline=True,
        )
        assert source == "snapshot"
