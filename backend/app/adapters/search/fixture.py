"""Deterministic offline search provider.

The canonical demo path and the only provider CI uses. Scoring is a simple
keyword overlap rather than exact query matching, so the provider answers any
question the planner produces while staying completely deterministic: the same
query always returns the same ranked results.

Results are labelled ``is_fixture=True`` everywhere they surface. A fixture is
never presented as a live retrieval.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from app.adapters.search.base import SearchResult
from app.core.logging import get_logger
from app.domain.enums import SearchMode

log = get_logger(__name__)

CORPUS_PATH = Path(__file__).parent / "fixtures" / "corpus.json"

#: Words carrying no discriminating signal for this corpus.
_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "best",
        "but",
        "by",
        "can",
        "compare",
        "comparison",
        "do",
        "does",
        "for",
        "from",
        "how",
        "in",
        "is",
        "it",
        "its",
        "of",
        "on",
        "or",
        "should",
        "small",
        "team",
        "teams",
        "that",
        "the",
        "their",
        "them",
        "these",
        "this",
        "to",
        "use",
        "using",
        "was",
        "were",
        "what",
        "when",
        "which",
        "who",
        "why",
        "will",
        "with",
        "would",
        "you",
        "your",
    }
)

_TOKEN = re.compile(r"[a-z0-9]+")


#: Shortest shared prefix that counts as a partial keyword match. Substring
#: matching was too loose -- "chromodynamics" contains "dynamic" -- so a
#: shared prefix is used instead, which still relates "tests" to "testing"
#: and "operations" to "operational".
_MIN_SHARED_PREFIX = 4


def _shares_prefix(left: str, right: str) -> bool:
    if min(len(left), len(right)) < _MIN_SHARED_PREFIX:
        return False
    shared = 0
    for a, b in zip(left, right, strict=False):
        if a != b:
            break
        shared += 1
    return shared >= _MIN_SHARED_PREFIX


def _tokenise(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower()) if t not in _STOPWORDS and len(t) > 2]


@dataclass(frozen=True, slots=True)
class FixtureDocument:
    id: str
    url: str
    title: str
    excerpt: str
    keywords: tuple[str, ...]
    redteam: bool = False

    def score(self, query_tokens: list[str]) -> float:
        """Overlap score. Keywords weigh most, then the title, then the body.

        Deliberately simple and pure: the ranking must be reproducible across
        machines and Python versions for the offline demo to be meaningful.
        """
        if not query_tokens:
            return 0.0
        keywords = set(self.keywords)
        title_tokens = set(_tokenise(self.title))
        body_tokens = set(_tokenise(self.excerpt))

        total = 0.0
        for token in query_tokens:
            if token in keywords:
                total += 3.0
            elif any(_shares_prefix(token, keyword) for keyword in keywords):
                total += 1.5
            if token in title_tokens:
                total += 2.0
            if token in body_tokens:
                total += 1.0
        return total / len(query_tokens)


class FixtureSearchProvider:
    """A :class:`~app.adapters.search.base.SearchProvider` over a committed corpus."""

    def __init__(self, documents: list[FixtureDocument] | None = None) -> None:
        self._documents = documents if documents is not None else load_corpus()

    @property
    def mode(self) -> SearchMode:
        return SearchMode.FIXTURE

    async def search(self, query: str, *, max_results: int) -> list[SearchResult]:
        tokens = _tokenise(query)
        scored = [(doc.score(tokens), doc) for doc in self._documents]
        # Tie-break on document id so ordering is total and reproducible.
        ranked = sorted(
            (pair for pair in scored if pair[0] > 0.0),
            key=lambda pair: (-pair[0], pair[1].id),
        )[:max_results]

        results = [
            SearchResult(
                url=doc.url,
                title=doc.title,
                excerpt=doc.excerpt,
                rank=index,
                provider="fixture",
                is_fixture=True,
            )
            for index, (_, doc) in enumerate(ranked)
        ]
        log.info(
            "search_completed",
            provider="fixture",
            query_tokens=len(tokens),
            results=len(results),
        )
        return results

    async def aclose(self) -> None:
        return None


def load_corpus(path: Path | None = None) -> list[FixtureDocument]:
    """Load the committed corpus from disk."""
    target = path or CORPUS_PATH
    with target.open(encoding="utf-8") as handle:
        raw = json.load(handle)
    return [
        FixtureDocument(
            id=doc["id"],
            url=doc["url"],
            title=doc["title"],
            excerpt=doc["excerpt"],
            keywords=tuple(doc.get("keywords", [])),
            redteam=bool(doc.get("redteam", False)),
        )
        for doc in raw["documents"]
    ]
