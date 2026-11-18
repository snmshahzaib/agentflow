"""Application configuration.

Everything environment-dependent is resolved here, once, at startup. In
particular **no model ID appears anywhere else in the codebase** — node code
asks :meth:`Settings.model_for_node` and never names a vendor or a model.

The settings object is cached, so importing it is cheap; tests override it by
clearing the cache (see ``tests/conftest.py``).
"""

from __future__ import annotations

from collections.abc import Mapping
from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, SecretStr, computed_field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.domain.enums import DataCollectionPolicy, LLMProvider, SearchMode
from app.domain.nodes import NodeName

#: The environment variable that overrides each node's model. This mapping is
#: authoritative: ``Settings`` reads its fields through it and validation
#: error messages are generated from it, so a message can never name a
#: variable that does not exist.
NODE_MODEL_ENV_VARS: Mapping[NodeName, str] = {
    NodeName.PLAN: "LLM_MODEL_PLANNER",
    NodeName.ANALYSE: "LLM_MODEL_ANALYST",
    NodeName.CITATION_CHECK: "LLM_MODEL_CITATION_CHECK",
    NodeName.DRAFT: "LLM_MODEL_DRAFT",
}

#: Smallest context window AgentFlow will accept for any node. A research task
#: can carry eight source excerpts plus a drafted report into a single call.
MIN_CONTEXT_TOKENS = 200_000


class Settings(BaseSettings):
    """Validated application settings, assembled from the environment."""

    model_config = SettingsConfigDict(
        env_file=(".env",),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- core -----------------------------------------------------------
    app_env: Literal["dev", "test", "production"] = "dev"
    log_level: Literal["debug", "info", "warning", "error"] = "info"
    log_format: Literal["json", "console"] = "json"
    cors_allow_origins: str = "http://localhost:5173"

    # --- database -------------------------------------------------------
    database_url: str | None = None
    postgres_user: str = "agentflow"
    postgres_password: SecretStr = SecretStr("agentflow")
    postgres_db: str = "agentflow"
    postgres_host: str = "localhost"
    postgres_port: int = 5432

    # --- redis ----------------------------------------------------------
    redis_url: str = "redis://localhost:6379/0"

    # --- LLM gateway ----------------------------------------------------
    llm_provider: LLMProvider = LLMProvider.STUB
    llm_base_url: str = "https://openrouter.ai/api/v1"
    llm_api_key: SecretStr | None = None

    llm_model_default: str = ""
    llm_model_planner: str | None = None
    llm_model_analyst: str | None = None
    llm_model_citation_check: str | None = None
    llm_model_draft: str | None = None

    llm_fallback_models: str = ""
    llm_require_structured_output: bool = True
    #: Refuse to call any model that is not free. A hard control, not a
    #: preference: with it set, a billable call cannot be made even if a
    #: priced model is configured by mistake.
    llm_free_models_only: bool = False
    llm_timeout_seconds: Annotated[float, Field(gt=0, le=600)] = 90.0
    llm_max_attempts: Annotated[int, Field(ge=1, le=10)] = 3
    llm_data_collection: DataCollectionPolicy = DataCollectionPolicy.DENY
    llm_app_title: str = "AgentFlow"
    llm_app_url: str = ""
    llm_max_tokens_per_task: Annotated[int, Field(gt=0)] = 120_000
    llm_max_calls_per_task: Annotated[int, Field(gt=0)] = 12

    # --- search ---------------------------------------------------------
    search_provider: SearchMode = SearchMode.FIXTURE
    search_api_key: SecretStr | None = None
    search_max_results_per_subtask: Annotated[int, Field(ge=1, le=20)] = 5
    search_timeout_seconds: Annotated[float, Field(gt=0, le=120)] = 20.0
    search_base_url: str = "https://api.tavily.com"
    search_max_attempts: Annotated[int, Field(ge=1, le=10)] = 3
    #: "basic" costs 1 API credit against the free monthly allowance,
    #: "advanced" costs 2. Nothing in this workflow needs advanced retrieval.
    search_depth: Literal["basic", "advanced"] = "basic"
    #: Ceiling on live searches per task, mirroring LLM_MAX_CALLS_PER_TASK. A
    #: free allowance is a shared, exhaustible resource; a runaway plan should
    #: cost one task rather than the month.
    search_max_per_task: Annotated[int, Field(ge=1, le=50)] = 8
    #: Comma-separated domains never to retrieve from.
    search_exclude_domains: str = ""

    # --- orchestration --------------------------------------------------
    max_subtasks: Annotated[int, Field(ge=1, le=8)] = 4
    worker_max_concurrent_tasks: Annotated[int, Field(ge=1, le=32)] = 2
    node_max_attempts: Annotated[int, Field(ge=1, le=10)] = 3
    #: How many times a reviewer may send a report back. Bounded because each
    #: revision re-plans and re-retrieves, so an unbounded loop would be an
    #: unbounded spend.
    max_revision_rounds: Annotated[int, Field(ge=0, le=10)] = 3
    node_timeout_seconds: Annotated[float, Field(gt=0)] = 120.0

    # --- demo safety ----------------------------------------------------
    #: Enforced at admission (app.services.admission), before a task row is
    #: written or a job is queued.
    #:
    #: The daily ceilings are deployment-wide, counted per UTC day in the
    #: `demo_budget` table. The spend ceiling uses the ledger's own estimate,
    #: so it bounds what the deployment can cost rather than what it intended
    #: to cost.
    demo_daily_budget_usd: Annotated[float, Field(ge=0)] = 2.0
    demo_daily_task_limit: Annotated[int, Field(ge=1)] = 100
    demo_max_tasks_per_session: Annotated[int, Field(ge=1)] = 5

    #: Gates **live** research. Unset, a deployment configured with live
    #: providers still serves every anonymous visitor the deterministic
    #: offline profile; a task is only allowed to reach a real provider when
    #: the request carries this code. Public demos must set it.
    demo_access_code: SecretStr | None = None

    #: Enforced on write endpoints, keyed by session. See ADR-017.
    rate_limit_per_minute: Annotated[int, Field(ge=1)] = 20
    session_secret: SecretStr = SecretStr("dev-only-insecure-session-secret")
    #: Enforced by the nightly retention sweep (app.workers.jobs).
    data_retention_days: Annotated[int, Field(ge=1)] = 7

    # ------------------------------------------------------------------
    # Validators
    # ------------------------------------------------------------------

    @field_validator("llm_api_key", "search_api_key", "demo_access_code", mode="before")
    @classmethod
    def _blank_or_placeholder_is_none(cls, v: object) -> object:
        """Treat empty strings and ``.env.example`` placeholders as unset.

        Without this, copying ``.env.example`` verbatim would look like a
        configured key and fail much later with a confusing provider error.
        """
        if isinstance(v, str):
            s = v.strip()
            if not s or (s.startswith("__") and s.endswith("__")):
                return None
        return v

    @model_validator(mode="after")
    def _check_live_provider_requirements(self) -> Settings:
        if self.llm_provider is not LLMProvider.STUB:
            if self.llm_api_key is None:
                raise ValueError(
                    f"LLM_PROVIDER={self.llm_provider.value} requires LLM_API_KEY. "
                    "Set LLM_PROVIDER=stub to run offline without a key."
                )
            if not self.llm_model_default.strip():
                raise ValueError(
                    f"LLM_PROVIDER={self.llm_provider.value} requires LLM_MODEL_DEFAULT "
                    "(see docs/model-configuration.md)."
                )
        if self.search_provider is not SearchMode.FIXTURE and self.search_api_key is None:
            raise ValueError(
                f"SEARCH_PROVIDER={self.search_provider.value} requires SEARCH_API_KEY. "
                "Set SEARCH_PROVIDER=fixture to run offline without a key."
            )
        if self.app_env == "production":
            if self.session_secret.get_secret_value() == "dev-only-insecure-session-secret":
                raise ValueError(
                    "SESSION_SECRET must be set in production "
                    "(generate with: openssl rand -hex 32)."
                )
            if not self.llm_require_structured_output:
                raise ValueError(
                    "LLM_REQUIRE_STRUCTURED_OUTPUT must stay true in production: "
                    "schema-constrained output is part of the prompt-injection boundary "
                    "(docs/security.md)."
                )
        return self

    # ------------------------------------------------------------------
    # Derived values
    # ------------------------------------------------------------------

    @computed_field  # type: ignore[prop-decorator]
    @property
    def sqlalchemy_url(self) -> str:
        """Async SQLAlchemy DSN, from ``DATABASE_URL`` or assembled from parts."""
        if self.database_url:
            return self.database_url
        pwd = self.postgres_password.get_secret_value()
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{pwd}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def checkpointer_dsn(self) -> str:
        """libpq connection string for LangGraph's Postgres checkpointer.

        The checkpointer takes a raw DSN, not a SQLAlchemy URL, so this
        deliberately carries no ``+driver`` suffix.
        """
        return self.sqlalchemy_url.replace("postgresql+asyncpg://", "postgresql://", 1)

    @property
    def alembic_url(self) -> str:
        """Sync SQLAlchemy URL for migrations.

        Migrations run synchronously on psycopg 3. The ``+psycopg`` suffix is
        required: a bare ``postgresql://`` URL resolves to psycopg2, which
        this project does not install.
        """
        return self.sqlalchemy_url.replace("postgresql+asyncpg://", "postgresql+psycopg://", 1)

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.cors_allow_origins.split(",") if o.strip()]

    @property
    def excluded_search_domains(self) -> list[str]:
        """Domains never to retrieve from."""
        return [d.strip().lower() for d in self.search_exclude_domains.split(",") if d.strip()]

    @property
    def fallback_models(self) -> list[str]:
        """Ordered, de-duplicated fallback chain."""
        seen: dict[str, None] = {}
        for raw in self.llm_fallback_models.split(","):
            slug = raw.strip()
            if slug and slug != self.llm_model_default:
                seen.setdefault(slug, None)
        return list(seen)

    def model_for_node(self, node: NodeName) -> str:
        """Resolve the model for ``node``: per-node override, else the default.

        The only place in the codebase that decides which model runs where.
        """
        env_var = NODE_MODEL_ENV_VARS.get(node)
        override = getattr(self, env_var.lower(), None) if env_var else None
        # A blank or whitespace-only override falls back rather than resolving
        # to an empty model ID.
        return (override or "").strip() or self.llm_model_default.strip()

    def configured_models(self) -> dict[NodeName, str]:
        """Every node that calls a model, mapped to its resolved slug."""
        from app.domain.nodes import LLM_NODES

        return {node: self.model_for_node(node) for node in sorted(LLM_NODES)}

    @property
    def is_offline(self) -> bool:
        """True when neither the model nor the search path touches the network."""
        return self.llm_provider is LLMProvider.STUB and self.search_provider is SearchMode.FIXTURE


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings accessor. Clear the cache in tests to override."""
    return Settings()
