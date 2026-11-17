"""A Redis fixed-window rate limiter.

Fixed window, not a token bucket: the counter is one key per window per
caller, incremented atomically and expired by Redis, so there is no state to
sweep and no clock to coordinate. A fixed window admits a burst across a
window boundary -- up to twice the limit over two adjacent windows -- which is
an acceptable trade for a guard whose job is to stop sustained hammering
rather than to shape traffic precisely.

Two deliberate choices:

* **Redis server time**, not process time. The counter must agree across API
  replicas, and a skewed container clock would otherwise give one replica its
  own window.
* **Fails open.** A limiter that rejects requests when its own backing store
  is unreachable turns a Redis blip into an outage. The provider allowance is
  protected by hard per-task and per-session ceilings either way, so the
  limiter is a convenience control, not the last line.
"""

from __future__ import annotations

from redis.asyncio import Redis

from app.adapters.ratelimit.base import RateLimitDecision
from app.core.logging import get_logger

log = get_logger(__name__)

KEY_PREFIX = "agentflow:ratelimit"


class RedisWindowRateLimiter:
    """Counts requests per caller per fixed window."""

    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def check(self, key: str, *, limit: int, window_seconds: int) -> RateLimitDecision:
        try:
            seconds, _ = await self._redis.time()
            # The window id comes from the clock, so a fresh count appears
            # when the window turns over; nothing has to reset the old one.
            window = int(seconds) // window_seconds
            redis_key = f"{KEY_PREFIX}:{key}:{window}"
            pipe = self._redis.pipeline()
            pipe.incr(redis_key)
            pipe.expire(redis_key, window_seconds)
            used = int((await pipe.execute())[0])
            reset_in = window_seconds - (int(seconds) % window_seconds)
        except Exception as exc:
            log.warning("rate_limit_unavailable", error=str(exc)[:200], failing_open=True)
            return RateLimitDecision(
                allowed=True, used=0, limit=limit, retry_after_seconds=window_seconds
            )

        decision = RateLimitDecision(
            allowed=used <= limit,
            used=used,
            limit=limit,
            retry_after_seconds=max(1, reset_in),
        )
        if not decision.allowed:
            log.info("rate_limited", used=used, limit=limit, retry_after_seconds=reset_in)
        return decision
