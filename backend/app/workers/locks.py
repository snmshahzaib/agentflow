"""Single-flight task locking.

Exactly one worker may execute a given task at a time. Combined with
``UNIQUE(task_id, node, attempt)`` and version-derived reports, this is what
makes a re-delivered or retried job harmless rather than a source of duplicate
steps and reports.

The lock is held on a **short TTL refreshed by a heartbeat** rather than a
long one. A worker killed with SIGKILL never runs its release path, so with a
long TTL its task would stay locked — and therefore unrecoverable — for as
long as that TTL. A short TTL plus a heartbeat means a dead worker's lock
expires in about a minute, which is what lets the orphan sweep pick the task
back up.
"""

from __future__ import annotations

import asyncio
import contextlib
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

#: Extends the TTL only while this worker still owns the lock.
_REFRESH_IF_OWNED = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('expire', KEYS[1], ARGV[2])
end
return 0
"""


async def _heartbeat(redis: Any, key: str, token: str, ttl: int, interval: float) -> None:
    """Keep the lock alive while this worker is genuinely working."""
    while True:
        await asyncio.sleep(interval)
        try:
            extended = await redis.eval(_REFRESH_IF_OWNED, 1, key, token, ttl)
            if not extended:
                # Someone else owns it now: stop refreshing rather than fight.
                log.warning("lock_lost", key=key)
                return
        except Exception:  # pragma: no cover - a transient Redis blip
            log.warning("lock_heartbeat_failed", key=key)


@asynccontextmanager
async def single_flight(
    redis: Any, key: str, *, ttl: int, heartbeat_interval: float | None = None
) -> AsyncIterator[bool]:
    """Hold an exclusive lock on ``key``, yielding whether it was acquired.

    While held, a background heartbeat extends the TTL. If this process dies
    the heartbeat stops with it and the lock expires within ``ttl``.
    """
    token = uuid.uuid4().hex
    acquired = bool(await redis.set(key, token, nx=True, ex=ttl))

    beat: asyncio.Task[None] | None = None
    if acquired:
        interval = heartbeat_interval if heartbeat_interval is not None else max(ttl / 3, 5)
        beat = asyncio.create_task(_heartbeat(redis, key, token, ttl, interval))

    try:
        yield acquired
    finally:
        if beat is not None:
            beat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await beat
        if acquired:
            try:
                await redis.eval(_RELEASE_IF_OWNED, 1, key, token)
            except Exception:  # pragma: no cover - lock expiry is not fatal
                log.warning("lock_release_failed", key=key)


async def is_locked(redis: Any, key: str) -> bool:
    """Whether a worker currently holds ``key``."""
    return bool(await redis.exists(key))
