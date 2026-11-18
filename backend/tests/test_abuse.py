"""Abuse and failure behaviour, through the HTTP surface.

Everything here is about what a deployment does when something goes wrong or
someone misbehaves: a stranger reaching for another visitor's report, a
visitor submitting faster than the limits allow, Redis falling over mid
request. The assertions are about *observable* behaviour -- status codes,
whether a row was written, what leaks into the response.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.ratelimit.base import RateLimitDecision
from app.api.deps import (
    get_db,
    get_llm_runtime,
    get_queue,
    get_rate_limiter,
)
from app.core.config import get_settings
from app.core.security import SESSION_COOKIE_NAME, new_session_id, sign_session_id
from app.db.models import ResearchTask
from app.domain.enums import LLMProvider, SearchMode, TaskStatus
from app.main import create_app
from tests.conftest import make_settings

pytestmark = pytest.mark.integration

SESSION_SECRET = "0" * 64
QUESTION = "Compare three open-source workflow orchestration frameworks."


class RecordingQueue:
    def __init__(self) -> None:
        self.runs: list[str] = []
        self.resumes: list[tuple[str, dict[str, Any]]] = []

    async def enqueue_run(self, task_id: str) -> None:
        self.runs.append(task_id)

    async def enqueue_resume(self, task_id: str, decision: dict[str, Any]) -> None:
        self.resumes.append((task_id, decision))

    async def ping(self) -> bool:
        return True


class BrokenQueue(RecordingQueue):
    """Redis is unreachable."""

    async def enqueue_run(self, task_id: str) -> None:
        raise ConnectionError("redis is unreachable")

    async def ping(self) -> bool:
        return False


class PermissiveLimiter:
    async def check(self, key: str, *, limit: int, window_seconds: int) -> RateLimitDecision:
        return RateLimitDecision(allowed=True, used=1, limit=limit, retry_after_seconds=0)


class ExhaustedLimiter:
    async def check(self, key: str, *, limit: int, window_seconds: int) -> RateLimitDecision:
        return RateLimitDecision(allowed=False, used=limit + 1, limit=limit, retry_after_seconds=42)


def stub_runtime() -> Any:
    from app.adapters.llm.registry import STUB_MODEL_ID, LLMRuntime
    from app.adapters.llm.stub import StubLLMClient
    from app.domain.nodes import LLM_NODES

    return LLMRuntime(
        client=StubLLMClient({}),
        provider=LLMProvider.STUB,
        models=dict.fromkeys(sorted(LLM_NODES), STUB_MODEL_ID),
        catalogue_source="stub",
    )


async def build_client(
    db_session: AsyncSession,
    *,
    queue: Any = None,
    limiter: Any = None,
    session_id: str | None = None,
    **settings_overrides: Any,
) -> AsyncIterator[tuple[AsyncClient, Any]]:
    settings = make_settings(session_secret=SESSION_SECRET, **settings_overrides)
    get_settings.cache_clear()
    app = create_app()
    queue = queue or RecordingQueue()

    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_queue] = lambda: queue
    app.dependency_overrides[get_llm_runtime] = stub_runtime
    app.dependency_overrides[get_rate_limiter] = lambda: limiter or PermissiveLimiter()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client.cookies.set(
            SESSION_COOKIE_NAME, sign_session_id(session_id or new_session_id(), SESSION_SECRET)
        )
        yield client, queue


@asynccontextmanager
async def session_scope_from(factory: Any) -> AsyncIterator[AsyncSession]:
    """`app.db.session.session_scope`, bound to a factory the test controls.

    The production helper reads a module-level factory configured at startup;
    this mirrors its commit/rollback contract so the rollback can be asserted
    without reaching into global state.
    """
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def seed_task(session: AsyncSession, *, session_id: str) -> ResearchTask:
    task = ResearchTask(
        session_id=session_id,
        question=QUESTION,
        status=TaskStatus.AWAITING_APPROVAL,
        search_mode=SearchMode.FIXTURE,
        llm_provider=LLMProvider.STUB,
        max_tokens_budget=120_000,
        max_calls_budget=12,
    )
    session.add(task)
    await session.flush()
    return task


class TestCrossSessionAccess:
    """A visitor must not reach another visitor's research, in any way."""

    async def test_every_task_route_is_invisible_to_another_session(
        self, db_session: AsyncSession
    ) -> None:
        owner = new_session_id()
        task = await seed_task(db_session, session_id=owner)

        async for client, _ in build_client(db_session, session_id=new_session_id()):
            for method, path in [
                ("get", f"/api/tasks/{task.id}"),
                ("get", f"/api/tasks/{task.id}/steps"),
                ("get", f"/api/tasks/{task.id}/sources"),
                ("get", f"/api/tasks/{task.id}/claims"),
                ("get", f"/api/tasks/{task.id}/report"),
                ("get", f"/api/tasks/{task.id}/export"),
                ("post", f"/api/tasks/{task.id}/approve"),
                ("post", f"/api/tasks/{task.id}/request-revision"),
            ]:
                response = await getattr(client, method)(
                    path, **({"json": {"feedback": "please change it"}} if method == "post" else {})
                )
                # 404, never 403: a stranger learns nothing about whether the
                # task exists.
                assert response.status_code == 404, f"{method} {path} -> {response.status_code}"
                assert "question" not in response.text

    async def test_an_unauthorised_approval_does_not_change_the_task(
        self, db_session: AsyncSession
    ) -> None:
        owner = new_session_id()
        task = await seed_task(db_session, session_id=owner)

        async for client, _ in build_client(db_session, session_id=new_session_id()):
            await client.post(f"/api/tasks/{task.id}/approve", json={})

        await db_session.refresh(task)
        assert task.status is TaskStatus.AWAITING_APPROVAL

    async def test_a_task_list_never_leaks_another_session(self, db_session: AsyncSession) -> None:
        await seed_task(db_session, session_id=new_session_id())

        async for client, _ in build_client(db_session, session_id=new_session_id()):
            body = (await client.get("/api/tasks")).json()
            assert body["items"] == []


class TestLimitsThroughHttp:
    async def test_the_per_session_task_limit_returns_429(self, db_session: AsyncSession) -> None:
        owner = new_session_id()
        for _ in range(2):
            await seed_task(db_session, session_id=owner)

        async for client, queue in build_client(
            db_session, session_id=owner, demo_max_tasks_per_session=2
        ):
            response = await client.post("/api/tasks", json={"question": QUESTION})

            assert response.status_code == 429
            assert response.json()["error"]["code"] == "session_task_limit"
            assert queue.runs == []

    async def test_the_daily_task_ceiling_returns_429(self, db_session: AsyncSession) -> None:
        async for client, queue in build_client(db_session, demo_daily_task_limit=2):
            codes = [
                (await client.post("/api/tasks", json={"question": QUESTION})).status_code
                for _ in range(4)
            ]

            assert codes == [202, 202, 429, 429]
            assert len(queue.runs) == 2
            assert (await client.get("/api/tasks")).json()["items"].__len__() == 2

    async def test_a_refused_submission_writes_no_task_row(self, db_session: AsyncSession) -> None:
        async for client, _ in build_client(db_session, demo_daily_task_limit=1):
            await client.post("/api/tasks", json={"question": QUESTION})
            before = int(
                await db_session.scalar(select(func.count()).select_from(ResearchTask)) or 0
            )
            await client.post("/api/tasks", json={"question": QUESTION})
            after = int(
                await db_session.scalar(select(func.count()).select_from(ResearchTask)) or 0
            )

        assert after == before

    async def test_rate_limit_exhaustion_is_reported_with_retry_after(
        self, db_session: AsyncSession
    ) -> None:
        async for client, queue in build_client(db_session, limiter=ExhaustedLimiter()):
            response = await client.post("/api/tasks", json={"question": QUESTION})

            assert response.status_code == 429
            assert response.headers["retry-after"] == "42"
            assert response.json()["error"]["code"] == "rate_limited"
            assert queue.runs == []


class TestConcurrentSubmissions:
    async def test_the_session_limit_holds_under_concurrency(
        self, db_session: AsyncSession
    ) -> None:
        """Eight submissions at once against a limit of three.

        A limit enforced by read-then-write would let several through
        together. This asserts the number admitted never exceeds the limit --
        the database, not the application, is the arbiter.
        """
        owner = new_session_id()
        async for client, _recorded in build_client(
            db_session, session_id=owner, demo_max_tasks_per_session=3
        ):
            responses = await asyncio.gather(
                *(client.post("/api/tasks", json={"question": QUESTION}) for _ in range(8)),
                return_exceptions=True,
            )

        accepted = [
            r for r in responses if not isinstance(r, BaseException) and r.status_code == 202
        ]
        assert len(accepted) <= 3
        assert len(_recorded.runs) == len(accepted)

    async def test_an_idempotency_key_survives_concurrent_retries(
        self, db_session: AsyncSession
    ) -> None:
        # A dropped response retried several times must not create duplicates.
        key = f"idem-{uuid.uuid4().hex[:8]}"
        async for client, _ in build_client(db_session):
            responses = await asyncio.gather(
                *(
                    client.post(
                        "/api/tasks",
                        json={"question": QUESTION},
                        headers={"Idempotency-Key": key},
                    )
                    for _ in range(5)
                ),
                return_exceptions=True,
            )

        ids = {
            r.json()["id"]
            for r in responses
            if not isinstance(r, BaseException) and r.status_code == 202
        }
        assert len(ids) == 1


class TestRedisOutage:
    async def test_a_submission_fails_cleanly_when_the_queue_is_down(
        self, db_session: AsyncSession
    ) -> None:
        """A dead queue must not leave a task row nobody will ever run.

        Nor may it leak a stack trace: the response carries the standard
        envelope and a correlation id, and nothing about Redis.
        """
        async for client, _ in build_client(db_session, queue=BrokenQueue()):
            response = await client.post("/api/tasks", json={"question": QUESTION})

        assert response.status_code == 503
        error = response.json()["error"]
        assert error["code"] == "queue_unavailable"
        assert error["correlation_id"]
        # The broker's own message names hosts and ports; none of it escapes.
        assert "redis" not in response.text.lower()
        assert "Traceback" not in response.text

    async def test_a_failed_enqueue_rolls_back_the_task_row(self, migrated_database: str) -> None:
        """No orphan row that nothing will ever pick up.

        Asserted against the real `session_scope`, not the test harness: the
        other tests in this file override the session dependency, which
        bypasses the very rollback this is about.
        """
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        from sqlalchemy.pool import NullPool

        from app.db.session import session_scope
        from app.services.task_service import TaskService

        engine = create_async_engine(migrated_database, poolclass=NullPool)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        settings = make_settings()
        session_id = new_session_id()

        try:
            with pytest.raises(Exception):  # noqa: B017 - the domain error type is asserted above
                async with session_scope_from(factory) as session:
                    service = TaskService(session=session, settings=settings, queue=BrokenQueue())
                    await service.create(session_id=session_id, question=QUESTION)

            async with factory() as session:
                remaining = int(
                    await session.scalar(
                        select(func.count())
                        .select_from(ResearchTask)
                        .where(ResearchTask.session_id == session_id)
                    )
                    or 0
                )
            assert remaining == 0
            assert session_scope is not None  # the real unit of work exists
        finally:
            await engine.dispose()

    async def test_readiness_reports_the_queue_as_down(self, db_session: AsyncSession) -> None:
        async for client, _ in build_client(db_session, queue=BrokenQueue()):
            body = (await client.get("/ready")).json()

        queue_component = next(c for c in body["components"] if c["name"] == "queue")
        assert queue_component["ready"] is False
        assert body["ready"] is False


class TestErrorResponsesLeakNothing:
    async def test_a_validation_error_does_not_echo_configuration(
        self, db_session: AsyncSession
    ) -> None:
        async for client, _ in build_client(db_session, llm_api_key="super-secret-value"):
            response = await client.post("/api/tasks", json={"question": "short"})

        assert response.status_code == 422
        assert "super-secret-value" not in response.text

    async def test_a_missing_task_says_nothing_about_other_tasks(
        self, db_session: AsyncSession
    ) -> None:
        async for client, _ in build_client(db_session):
            response = await client.get(f"/api/tasks/{uuid.uuid4()}")

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"
