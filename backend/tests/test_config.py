"""Configuration resolution and guard rails.

These tests encode the project rule that models are configuration, not code:
resolution order, fallback parsing, and the startup guards that refuse an
unusable or unsafe configuration.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.domain.enums import LLMProvider, SearchMode
from app.domain.nodes import LLM_NODES, NodeName
from tests.conftest import make_settings


class TestOfflineDefaults:
    def test_stub_provider_needs_no_credentials(self) -> None:
        s = make_settings()
        assert s.llm_provider is LLMProvider.STUB
        assert s.search_provider is SearchMode.FIXTURE
        assert s.llm_api_key is None
        assert s.is_offline is True

    def test_live_provider_is_not_offline(self) -> None:
        s = make_settings(
            llm_provider="openrouter",
            llm_api_key="test-key",
            llm_model_default="vendor/model-a",
        )
        assert s.is_offline is False


class TestCredentialGuards:
    def test_live_llm_without_key_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="requires LLM_API_KEY"):
            make_settings(llm_provider="openrouter", llm_model_default="vendor/model-a")

    def test_live_llm_without_default_model_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="requires LLM_MODEL_DEFAULT"):
            make_settings(llm_provider="openrouter", llm_api_key="test-key")

    def test_live_search_without_key_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="requires SEARCH_API_KEY"):
            make_settings(search_provider="tavily")

    @pytest.mark.parametrize(
        "placeholder",
        ["", "   ", "__SET_YOUR_OPENROUTER_API_KEY__", "__GENERATE_WITH_openssl_rand_hex_32__"],
    )
    def test_example_placeholders_count_as_unset(self, placeholder: str) -> None:
        """Copying .env.example verbatim must fail fast, not look configured."""
        s = make_settings(llm_api_key=placeholder)
        assert s.llm_api_key is None

    def test_a_real_looking_key_is_kept(self) -> None:
        s = make_settings(llm_api_key="unit-test-credential-value")
        assert s.llm_api_key is not None
        assert s.llm_api_key.get_secret_value() == "unit-test-credential-value"


class TestProductionGuards:
    def test_production_refuses_the_development_session_secret(self) -> None:
        with pytest.raises(ValidationError, match="SESSION_SECRET"):
            make_settings(app_env="production")

    def test_production_refuses_relaxed_structured_output(self) -> None:
        """Tier-3 output weakens the injection boundary; it may not ship."""
        with pytest.raises(ValidationError, match="LLM_REQUIRE_STRUCTURED_OUTPUT"):
            make_settings(
                app_env="production",
                session_secret="0" * 64,
                llm_require_structured_output=False,
            )

    def test_production_accepts_a_safe_configuration(self) -> None:
        s = make_settings(app_env="production", session_secret="0" * 64)
        assert s.app_env == "production"


class TestModelResolution:
    def test_every_llm_node_resolves_to_the_default(self) -> None:
        s = make_settings(llm_model_default="vendor/model-default")
        resolved = s.configured_models()
        assert set(resolved) == set(LLM_NODES)
        assert set(resolved.values()) == {"vendor/model-default"}

    def test_research_node_has_no_model(self) -> None:
        """The research node queries the search port; it never calls a model."""
        assert NodeName.RESEARCH not in LLM_NODES
        assert NodeName.RESEARCH not in make_settings().configured_models()

    def test_per_node_override_wins_over_the_default(self) -> None:
        s = make_settings(
            llm_model_default="vendor/model-default",
            llm_model_planner="vendor/model-small",
        )
        assert s.model_for_node(NodeName.PLAN) == "vendor/model-small"
        assert s.model_for_node(NodeName.DRAFT) == "vendor/model-default"

    @pytest.mark.parametrize(
        ("node", "field"),
        [
            (NodeName.PLAN, "llm_model_planner"),
            (NodeName.ANALYSE, "llm_model_analyst"),
            (NodeName.CITATION_CHECK, "llm_model_citation_check"),
            (NodeName.DRAFT, "llm_model_draft"),
        ],
    )
    def test_each_node_has_its_own_override(self, node: NodeName, field: str) -> None:
        s = make_settings(llm_model_default="vendor/model-default", **{field: "vendor/picked"})
        assert s.model_for_node(node) == "vendor/picked"

    def test_blank_override_falls_back_to_the_default(self) -> None:
        s = make_settings(llm_model_default="vendor/model-default", llm_model_draft="  ")
        assert s.model_for_node(NodeName.DRAFT) == "vendor/model-default"


class TestFallbackChain:
    def test_chain_is_ordered_and_trimmed(self) -> None:
        s = make_settings(
            llm_model_default="vendor/primary",
            llm_fallback_models=" vendor/second , other/third ",
        )
        assert s.fallback_models == ["vendor/second", "other/third"]

    def test_chain_excludes_the_primary_and_deduplicates(self) -> None:
        s = make_settings(
            llm_model_default="vendor/primary",
            llm_fallback_models="vendor/primary,vendor/second,vendor/second",
        )
        assert s.fallback_models == ["vendor/second"]

    def test_empty_chain_is_empty(self) -> None:
        assert make_settings().fallback_models == []


class TestDerivedUrls:
    def test_dsn_is_assembled_from_parts(self) -> None:
        s = make_settings(
            postgres_user="u",
            postgres_password="p",
            postgres_host="db",
            postgres_port=5555,
            postgres_db="d",
        )
        assert s.sqlalchemy_url == "postgresql+asyncpg://u:p@db:5555/d"

    def test_explicit_database_url_wins(self) -> None:
        s = make_settings(database_url="postgresql+asyncpg://x:y@host:1/z")
        assert s.sqlalchemy_url == "postgresql+asyncpg://x:y@host:1/z"

    def test_checkpointer_and_alembic_use_a_sync_driver(self) -> None:
        """LangGraph's Postgres saver and Alembic both need psycopg, not asyncpg."""
        s = make_settings(database_url="postgresql+asyncpg://u:p@h:5432/d")
        assert s.psycopg_url == "postgresql://u:p@h:5432/d"
        assert s.alembic_url == s.psycopg_url

    def test_cors_origins_are_split_and_trimmed(self) -> None:
        s = make_settings(cors_allow_origins="http://a.test, http://b.test ,")
        assert s.cors_origins == ["http://a.test", "http://b.test"]


class TestLimits:
    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("max_subtasks", 0),
            ("max_subtasks", 9),
            ("llm_max_attempts", 0),
            ("llm_timeout_seconds", 0),
            ("llm_max_tokens_per_task", 0),
            ("worker_max_concurrent_tasks", 0),
        ],
    )
    def test_out_of_range_limits_are_refused(self, field: str, value: object) -> None:
        with pytest.raises(ValidationError):
            make_settings(**{field: value})

    def test_defaults_match_the_approved_plan(self) -> None:
        s = make_settings()
        assert s.max_subtasks == 4
        assert s.worker_max_concurrent_tasks == 2
        assert s.llm_require_structured_output is True
        assert s.data_retention_days == 7
