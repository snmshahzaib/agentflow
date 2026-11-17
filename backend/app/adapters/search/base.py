"""The search port.

Retrieval is performed by AgentFlow's own code, never by the model: the model
is given no tools and no network access, so the only text it ever sees is text
we fetched deliberately. That is a security property, not an implementation
detail (docs/security.md).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.adapters.search.urls import url_digest
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
    #: Provider relevance score, where the provider supplies one.
    score: float | None = None
    #: Publication date as the provider reported it, unparsed. Kept as text
    #: because providers disagree on format and a wrong parse would be worse
    #: than no date.
    published_date: str | None = None

    @property
    def url_sha256(self) -> str:
        """Digest of the **normalised** URL, used to deduplicate within a task.

        Normalised rather than raw: otherwise ``http://Example.com/a?utm_source=x``
        and ``https://example.com/a`` are two sources, two citations and two
        entries in one report's source list.
        """
        return url_digest(self.url)


class SearchProvider(Protocol):
    """Outbound port for retrieving evidence."""

    @property
    def mode(self) -> SearchMode: ...

    async def search(self, query: str, *, max_results: int) -> list[SearchResult]:
        """Return ranked results for ``query``, best first."""
        ...

    async def aclose(self) -> None: ...
