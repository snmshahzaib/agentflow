"""Search provider selection."""

from __future__ import annotations

from app.adapters.search.base import SearchProvider
from app.adapters.search.fixture import FixtureSearchProvider
from app.adapters.search.tavily import TavilySearchProvider
from app.core.config import Settings
from app.domain.enums import SearchMode
from app.domain.errors import ConfigurationError


def build_search_provider(settings: Settings) -> SearchProvider:
    """Build the configured provider.

    A missing credential or an unimplemented mode fails here, at startup,
    rather than when a research task first tries to retrieve something.
    """
    if settings.search_provider is SearchMode.FIXTURE:
        return FixtureSearchProvider()

    if settings.search_provider is SearchMode.TAVILY:
        if settings.search_api_key is None:
            raise ConfigurationError(
                "SEARCH_PROVIDER=tavily requires SEARCH_API_KEY. Set "
                "SEARCH_PROVIDER=fixture to run offline without a key.",
            )
        return TavilySearchProvider(
            api_key=settings.search_api_key.get_secret_value(),
            base_url=settings.search_base_url,
            timeout_seconds=settings.search_timeout_seconds,
            max_attempts=settings.search_max_attempts,
            search_depth=settings.search_depth,
            exclude_domains=settings.excluded_search_domains,
        )

    raise ConfigurationError(
        f"SEARCH_PROVIDER={settings.search_provider.value} is not implemented.",
        search_provider=settings.search_provider.value,
    )
