"""The search port.

Retrieval is performed by AgentFlow's own code, never by the model: the model
is given no tools and no network access, so the only text it ever sees is text
we fetched deliberately. That is a security property, not an implementation
detail (docs/security.md).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Protocol

from app.domain.enums import SearchMode


@dataclass(frozen=True, slots=True)
class SearchResult:
    """One retrieved source.

    ``excerpt`` is supplied by the provider. AgentFlow does not dereference
    the URL itself, which removes the SSRF surface rather than mitigating it.
    """

    url: str
    title: str
    excerpt: str
    rank: int
    provider: str
    is_fixture: bool

    @property
    def url_sha256(self) -> str:
        """Stable digest used to deduplicate a URL within a task."""
        return hashlib.sha256(self.url.encode()).hexdigest()


class SearchProvider(Protocol):
    """Outbound port for retrieving evidence."""

    @property
    def mode(self) -> SearchMode: ...

    async def search(self, query: str, *, max_results: int) -> list[SearchResult]:
        """Return ranked results for ``query``, best first."""
        ...

    async def aclose(self) -> None: ...
