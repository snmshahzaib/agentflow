"""Source reference binding.

The model is shown sources as labels (``S1``, ``S2``, ...) and returns those
labels. This module resolves them back to the sources actually retrieved for
the task, and **drops anything it cannot resolve**.

That is the fourth layer of the prompt-injection boundary: a model which has
been talked into inventing a citation — or into citing a document that was
never retrieved — produces a reference that simply does not resolve, so the
fabrication cannot reach a report.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass

from app.core.logging import get_logger

log = get_logger(__name__)

#: Labels are assigned by us, so the accepted shape is narrow on purpose.
_LABEL = re.compile(r"^S(\d{1,3})$")


def label_for(index: int) -> str:
    """Label shown to the model for the source at ``index`` (0-based)."""
    return f"S{index + 1}"


@dataclass(frozen=True, slots=True)
class BindingResult:
    """Resolved source ids plus whatever was rejected, for auditing."""

    source_ids: tuple[uuid.UUID, ...]
    rejected: tuple[str, ...]

    @property
    def has_rejections(self) -> bool:
        return bool(self.rejected)


def bind_source_refs(refs: list[str], ordered_source_ids: list[uuid.UUID]) -> BindingResult:
    """Resolve model-supplied labels against the retrieved source set.

    Unknown, malformed, out-of-range and duplicate labels are all dropped.
    Rejections are returned rather than raised: a single bad reference should
    weaken one claim, not fail the whole research task.
    """
    resolved: list[uuid.UUID] = []
    rejected: list[str] = []
    seen: set[uuid.UUID] = set()

    for raw in refs:
        match = _LABEL.match(raw.strip().upper()) if isinstance(raw, str) else None
        if match is None:
            rejected.append(str(raw)[:64])
            continue
        index = int(match.group(1)) - 1
        if index < 0 or index >= len(ordered_source_ids):
            rejected.append(str(raw)[:64])
            continue
        source_id = ordered_source_ids[index]
        if source_id in seen:
            continue
        seen.add(source_id)
        resolved.append(source_id)

    if rejected:
        log.warning(
            "citation_refs_rejected",
            rejected=rejected[:10],
            rejected_count=len(rejected),
            available=len(ordered_source_ids),
        )
    return BindingResult(source_ids=tuple(resolved), rejected=tuple(rejected))
