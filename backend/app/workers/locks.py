"""Single-flight task locking.

Exactly one worker may execute a given task at a time. Combined with
``UNIQUE(task_id, node, attempt)`` this is what makes a re-delivered or
retried job harmless rather than a source of duplicate steps and reports.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from app.core.logging import get_logger

log = get_logger(__name__)

#: Releases the lock only if this worker still owns it, so a worker whose lock
#: already expired cannot delete a lock another worker has since taken.
_RELEASE_IF_OWNED = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('del', KEYS[1])
end
return 0
"""


@asynccontextmanager
async def single_flight(redis: Any, key: str, *, ttl: int) -> AsyncIterator[bool]:
    """Hold an exclusive lock on ``key``, yielding whether it was acquired.

    The TTL bounds the damage from a crashed worker: the task becomes
    runnable again without manual intervention.
    """
    token = uuid.uuid4().hex
    acquired = bool(await redis.set(key, token, nx=True, ex=ttl))
    try:
        yield acquired
    finally:
        if acquired:
            try:
                await redis.eval(_RELEASE_IF_OWNED, 1, key, token)
            except Exception:  # pragma: no cover - lock expiry is not fatal
                log.warning("lock_release_failed", key=key)
