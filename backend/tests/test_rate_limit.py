"""Rate limiting: the window, the header, and what happens when Redis is gone."""

from __future__ import annotations

import os
import uuid
from typing import Any

import pytest
from redis.asyncio import Redis

from app.adapters.ratelimit.base import RateLimitDecision
from app.adapters.ratelimit.redis_window import RedisWindowRateLimiter

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")


def _fresh_key() -> str:
    """A key no earlier run can have touched.

    Counters outlive a test run by design -- Redis expires them with their
    window -- so reusing a fixed key makes the second run of the suite see the
    first run's count and fail.
    """
    return f"test:{uuid.uuid4().hex}"


class TestDecision:
    def test_remaining_never_goes_negative(self) -> None:
        # A caller well past the limit should see zero remaining, not a
        # negative number that a client might render as a countdown.
        decision = RateLimitDecision(allowed=False, used=25, limit=20, retry_after_seconds=12)
        assert decision.remaining == 0

    def test_remaining_counts_down_within_the_window(self) -> None:
        decision = RateLimitDecision(allowed=True, used=3, limit=20, retry_after_seconds=12)
        assert decision.remaining == 17


class _BrokenRedis:
    """Stands in for a Redis that is unreachable."""

    async def time(self) -> tuple[int, int]:
        raise ConnectionError("redis is down")


class TestFailureMode:
    async def test_an_unreachable_redis_fails_open(self) -> None:
        # A limiter that rejects when its own store is down converts a Redis
        # blip into an outage. The provider allowance is protected by hard
        # per-task and per-session ceilings regardless.
        limiter = RedisWindowRateLimiter(_BrokenRedis())  # type: ignore[arg-type]
        decision = await limiter.check("whoever", limit=1, window_seconds=60)
        assert decision.allowed is True
        assert decision.used == 0


@pytest.mark.integration
class TestAgainstRealRedis:
    @pytest.fixture
    async def limiter(self) -> Any:
        client: Redis = Redis.from_url(REDIS_URL, decode_responses=True)
        try:
            await client.ping()
        except Exception:  # pragma: no cover - environment guard
            pytest.skip("Redis is not reachable")
        try:
            yield RedisWindowRateLimiter(client), client
        finally:
            await client.aclose()

    async def test_requests_are_allowed_up_to_the_limit_then_refused(self, limiter: Any) -> None:
        rl, _ = limiter
        key = _fresh_key()

        decisions = [await rl.check(key, limit=3, window_seconds=60) for _ in range(5)]

        assert [d.allowed for d in decisions] == [True, True, True, False, False]
        assert [d.used for d in decisions] == [1, 2, 3, 4, 5]
        assert [d.remaining for d in decisions] == [2, 1, 0, 0, 0]

    async def test_a_refusal_says_when_to_come_back(self, limiter: Any) -> None:
        rl, _ = limiter
        key = _fresh_key()

        await rl.check(key, limit=1, window_seconds=60)
        refused = await rl.check(key, limit=1, window_seconds=60)

        assert refused.allowed is False
        # Within the window, never zero: a client told to wait 0 seconds
        # retries immediately, which is the behaviour the limit exists to stop.
        assert 1 <= refused.retry_after_seconds <= 60

    async def test_callers_are_counted_separately(self, limiter: Any) -> None:
        rl, _ = limiter
        first = await rl.check(_fresh_key(), limit=1, window_seconds=60)
        second = await rl.check(_fresh_key(), limit=1, window_seconds=60)
        assert first.allowed and second.allowed

    async def test_the_counter_expires_with_its_window(self, limiter: Any) -> None:
        rl, client = limiter
        key = _fresh_key()
        await rl.check(key, limit=5, window_seconds=60)
        keys = [k async for k in client.scan_iter(f"*{key}*")]
        assert keys, "the limiter should have written a counter"
        # Redis expires the counter itself, so there is no state to sweep.
        for k in keys:
            assert await client.ttl(k) > 0
