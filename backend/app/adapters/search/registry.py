"""Search provider selection."""

from __future__ import annotations

from app.adapters.search.base import SearchProvider
from app.adapters.search.fixture import FixtureSearchProvider
from app.core.config import Settings
from app.domain.enums import SearchMode
from app.domain.errors import ConfigurationError


def build_search_provider(settings: Settings) -> SearchProvider:
    """Build the configured provider.

    Only the fixture provider exists today; the live Tavily provider arrives
    in M4. An unimplemented mode fails at startup rather than when a research
    task first tries to retrieve something.
    """
    if settings.search_provider is SearchMode.FIXTURE:
        return FixtureSearchProvider()
    raise ConfigurationError(
        f"SEARCH_PROVIDER={settings.search_provider.value} is not implemented yet "
        "(live search arrives in M4). Set SEARCH_PROVIDER=fixture.",
        search_provider=settings.search_provider.value,
    )
