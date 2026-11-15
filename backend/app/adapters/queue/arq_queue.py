"""arq implementation of the queue port."""

from __future__ import annotations

from typing import Any

from arq.connections import ArqRedis, RedisSettings, create_pool

from app.core.logging import get_logger

log = get_logger(__name__)

RUN_TASK_JOB = "run_task"
RESUME_TASK_JOB = "resume_task"


class ArqTaskQueue:
    """Enqueues jobs onto Redis for the worker process to claim."""

    def __init__(self, pool: ArqRedis) -> None:
        self._pool = pool

    @classmethod
    async def connect(cls, redis_url: str) -> ArqTaskQueue:
        pool = await create_pool(RedisSettings.from_dsn(redis_url))
        return cls(pool)

    async def enqueue_run(self, task_id: str) -> None:
        # job_id makes enqueueing idempotent: a retried POST cannot schedule
        # the same task twice.
        await self._pool.enqueue_job(RUN_TASK_JOB, task_id, _job_id=f"run:{task_id}")
        log.info("task_enqueued", task_id=task_id, job=RUN_TASK_JOB)

    async def enqueue_resume(self, task_id: str, decision: dict[str, Any]) -> None:
        await self._pool.enqueue_job(RESUME_TASK_JOB, task_id, decision)
        log.info("task_resume_enqueued", task_id=task_id, job=RESUME_TASK_JOB)

    async def aclose(self) -> None:
        await self._pool.aclose()

    async def ping(self) -> bool:
        """Readiness probe: is Redis reachable?"""
        try:
            await self._pool.ping()
        except Exception:
            return False
        return True
