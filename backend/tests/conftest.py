"""Shared test fixtures.

Tests must never read a developer's real ``.env``: ``make_settings`` builds a
``Settings`` instance with dotenv loading disabled, so every test states its
own configuration explicitly.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from app.core.config import Settings, get_settings

#: Minimal configuration for an offline, key-free system.
OFFLINE_ENV: dict[str, Any] = {
    "app_env": "test",
    "llm_provider": "stub",
    "search_provider": "fixture",
    "log_format": "json",
}


def make_settings(**overrides: Any) -> Settings:
    """Build settings from explicit values only, ignoring any .env on disk."""
    return Settings(_env_file=None, **{**OFFLINE_ENV, **overrides})


@pytest.fixture
def settings() -> Settings:
    return make_settings()


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Iterator[None]:
    """Keep the cached singleton from leaking between tests."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
