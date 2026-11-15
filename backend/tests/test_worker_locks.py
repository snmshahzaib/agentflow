"""Single-flight task locking against real Redis.

This is what makes a re-delivered or retried job harmless: exactly one worker
may execute a given task at a time, and a worker may not release a lock it no
longer owns.
"""

from __future__ import annotations

import os

import pytest
from redis.asyncio import Redis

from app.workers.locks import single_flight

pytestmark = pytest.mark.integration

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")


@pytest.fixture
async def redis() -> Redis:
    client: Redis = Redis.from_url(REDIS_URL, decode_responses=True)
    try:
        await client.ping()
    except Exception:  # pragma: no cover
        pytest.skip("Redis is not reachable")
    return client


class TestSingleFlight:
    async def test_the_first_holder_acquires_the_lock(self, redis: Redis) -> None:
        key = "test:lock:acquire"
        await redis.delete(key)
        async with single_flight(redis, key, ttl=30) as acquired:
            assert acquired is True

    async def test_a_second_holder_is_refused_while_the_first_holds_it(self, redis: Redis) -> None:
        """Duplicate delivery is expected, not exceptional: the second job
        must no-op rather than become a second writer."""
        key = "test:lock:contended"
        await redis.delete(key)
        async with single_flight(redis, key, ttl=30) as first:
            assert first is True
            async with single_flight(redis, key, ttl=30) as second:
                assert second is False

    async def test_the_lock_is_released_on_exit(self, redis: Redis) -> None:
        key = "test:lock:release"
        await redis.delete(key)
        async with single_flight(redis, key, ttl=30):
            pass
        assert await redis.get(key) is None

    async def test_the_lock_is_released_even_when_the_body_raises(self, redis: Redis) -> None:
        """A crashing job must not leave a task permanently locked."""
        key = "test:lock:raise"
        await redis.delete(key)
        with pytest.raises(RuntimeError):
            async with single_flight(redis, key, ttl=30):
                raise RuntimeError("node failed")
        assert await redis.get(key) is None

    async def test_a_holder_cannot_release_a_lock_it_no_longer_owns(self, redis: Redis) -> None:
        """If this worker's lock expired and another worker took it, exiting
        must not delete the new owner's lock."""
        key = "test:lock:stolen"
        await redis.delete(key)
        async with single_flight(redis, key, ttl=30) as acquired:
            assert acquired
            # Simulate expiry followed by another worker claiming the task.
            await redis.set(key, "another-worker-token")
        assert await redis.get(key) == "another-worker-token"
        await redis.delete(key)

    async def test_the_lock_carries_a_ttl_so_a_crashed_worker_frees_it(self, redis: Redis) -> None:
        key = "test:lock:ttl"
        await redis.delete(key)
        async with single_flight(redis, key, ttl=42):
            assert 0 < await redis.ttl(key) <= 42
