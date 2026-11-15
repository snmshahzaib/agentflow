"""Runtime metadata.

``/api/meta`` exists so that nobody has to guess what produced a result: it
reports the resolved model for every node, where the catalogue came from, and
whether the system is running offline on fixtures and a stub.
"""

from __future__ import annotations

from pydantic import BaseModel

from app.domain.enums import LLMProvider, SearchMode


class NodeModelInfo(BaseModel):
    node: str
    model: str
    context_length: int | None = None
    supports_structured_outputs: bool | None = None
    prompt_usd_per_million: float | None = None
    completion_usd_per_million: float | None = None


class MetaResponse(BaseModel):
    version: str
    app_env: str

    llm_provider: LLMProvider
    search_provider: SearchMode
    #: True when neither the model nor retrieval touches the network. Output
    #: in this mode is deterministic fixture/stub content, never live AI.
    offline_mode: bool

    catalogue_source: str
    catalogue_size: int
    require_structured_output: bool
    models: list[NodeModelInfo]
    fallback_models: list[str]

    max_subtasks: int
    max_tokens_per_task: int
    max_calls_per_task: int


class HealthResponse(BaseModel):
    status: str


class ReadinessComponent(BaseModel):
    name: str
    ready: bool
    detail: str | None = None


class ReadinessResponse(BaseModel):
    ready: bool
    components: list[ReadinessComponent]
