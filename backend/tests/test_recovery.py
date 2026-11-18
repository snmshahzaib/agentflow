"""Worker execution, restart recovery and idempotency.

Exercises the real arq job handlers against real Postgres and Redis — the
code path M1 could only cover with a live smoke test. These are the M2
requirements that a re-delivered job, a crashed worker or a resumed run
cannot duplicate a step or a report.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from redis.asyncio import Redis
from sqlalchemy import func, select

from app.db.models import Report, ReportClaim, ResearchTask, WorkflowStep
from app.domain.enums import ApprovalAction, StepStatus, TaskStatus
from app.domain.nodes import NodeName
from app.graph.deps import NodeDeps
from app.workers.jobs import (
    LOCK_TTL_SECONDS,
    ORPHAN_GRACE_SECONDS,
    recover_orphaned_tasks,
    resume_task,
    run_task,
)
from app.workers.locks import single_flight
from tests.conftest import create_task_row

pytestmark = pytest.mark.integration

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
QUESTION = "Compare three open-source workflow orchestration frameworks for a small team."


@pytest.fixture
async def redis() -> AsyncIterator[Redis]:
    client: Redis = Redis.from_url(REDIS_URL, decode_responses=True)
    try:
        await client.ping()
    except Exception:  # pragma: no cover
        pytest.skip("Redis is not reachable")
    yield client
    await client.aclose()


class RecordingRedis:
    """Wraps the real Redis client and records enqueue_job calls.

    The sweep's job is to put work back on the queue; asserting on that is
    more precise than waiting for arq to pick it up.
    """

    def __init__(self, inner: Redis) -> None:
        self._inner = inner
        self.enqueued: list[tuple[str, str]] = []

    async def enqueue_job(self, name: str, *args: Any, **kwargs: Any) -> None:
        self.enqueued.append((name, str(args[0])))

    def __getattr__(self, item: str) -> Any:
        return getattr(self._inner, item)


@pytest.fixture
async def worker_ctx(
    node_deps: NodeDeps, redis: Redis, migrated_database: str
) -> AsyncIterator[dict[str, Any]]:
    """An arq context backed by a real Postgres checkpointer.

    A persistent checkpointer is the point: an in-memory one would make
    restart recovery untestable.
    """
    dsn = migrated_database.replace("postgresql+asyncpg://", "postgresql://", 1)
    async with AsyncPostgresSaver.from_conn_string(dsn) as checkpointer:
        await checkpointer.setup()
        yield {
            "redis": RecordingRedis(redis),
            "session_factory": node_deps.session_factory,
            "node_deps": node_deps,
            # The worker always builds both; a task admitted to the
            # offline profile must run there even on a live deployment.
            "offline_deps": node_deps,
            "checkpointer": checkpointer,
            "settings": node_deps.settings,
        }


async def steps_for(deps: NodeDeps, task_id: str, node: NodeName | None = None) -> int:
    async with deps.session_factory() as session:
        query = (
            select(func.count())
            .select_from(WorkflowStep)
            .where(WorkflowStep.task_id == uuid.UUID(task_id))
        )
        if node is not None:
            query = query.where(WorkflowStep.node == node)
        return int(await session.scalar(query) or 0)


async def reports_for(deps: NodeDeps, task_id: str) -> list[Report]:
    async with deps.session_factory() as session:
        return list(
            (
                await session.scalars(
                    select(Report)
                    .where(Report.task_id == uuid.UUID(task_id))
                    .order_by(Report.version)
                )
            ).all()
        )


class TestWorkerExecution:
    async def test_the_worker_runs_a_task_to_the_approval_gate(
        self, node_deps: NodeDeps, worker_ctx: dict[str, Any]
    ) -> None:
        task_id = await create_task_row(node_deps, question=QUESTION)
        result = await run_task(worker_ctx, task_id)

        assert result == TaskStatus.AWAITING_APPROVAL.value
        async with node_deps.session_factory() as session:
            task = await session.get(ResearchTask, uuid.UUID(task_id))
        assert task is not None
        assert task.status is TaskStatus.AWAITING_APPROVAL
        assert task.current_node is NodeName.DRAFT
        assert await steps_for(node_deps, task_id) == 5

    async def test_a_missing_task_is_reported_not_raised(self, worker_ctx: dict[str, Any]) -> None:
        """A job for a purged task must not crash the worker."""
        assert await run_task(worker_ctx, str(uuid.uuid4())) == "missing"


class TestDuplicateDelivery:
    async def test_a_second_job_is_refused_while_the_first_holds_the_lock(
        self, node_deps: NodeDeps, worker_ctx: dict[str, Any], redis: Redis
    ) -> None:
        """Duplicate delivery is expected, not exceptional."""
        task_id = await create_task_row(node_deps, question=QUESTION)
        async with single_flight(redis, f"task:{task_id}", ttl=LOCK_TTL_SECONDS) as held:
            assert held
            assert await run_task(worker_ctx, task_id) == "locked"
        assert await steps_for(node_deps, task_id) == 0

    async def test_redelivery_after_completion_does_not_rerun_the_task(
        self, node_deps: NodeDeps, worker_ctx: dict[str, Any]
    ) -> None:
        """The status guard stops a re-delivered job duplicating everything."""
        task_id = await create_task_row(node_deps, question=QUESTION)
        await run_task(worker_ctx, task_id)
        steps_after_first = await steps_for(node_deps, task_id)
        reports_after_first = await reports_for(node_deps, task_id)

        again = await run_task(worker_ctx, task_id)

        assert again == TaskStatus.AWAITING_APPROVAL.value
        assert await steps_for(node_deps, task_id) == steps_after_first
        assert len(await reports_for(node_deps, task_id)) == len(reports_after_first) == 1

    async def test_a_duplicate_resume_does_not_create_a_second_report(
        self, node_deps: NodeDeps, worker_ctx: dict[str, Any]
    ) -> None:
        task_id = await create_task_row(node_deps, question=QUESTION)
        await run_task(worker_ctx, task_id)

        decision = {"action": ApprovalAction.APPROVE.value}
        await resume_task(worker_ctx, task_id, decision)
        await resume_task(worker_ctx, task_id, decision)

        reports = await reports_for(node_deps, task_id)
        assert len(reports) == 1, "approval must not produce extra report versions"


class TestRestartRecovery:
    async def test_a_fresh_worker_resumes_from_the_persisted_checkpoint(
        self,
        node_deps: NodeDeps,
        worker_ctx: dict[str, Any],
        redis: Redis,
        migrated_database: str,
    ) -> None:
        """Simulates `docker compose restart worker`: the first process runs
        to the gate, then a completely new context -- new checkpointer
        connection, new graph -- resumes the same task."""
        task_id = await create_task_row(node_deps, question=QUESTION)
        await run_task(worker_ctx, task_id)
        steps_before = await steps_for(node_deps, task_id)

        dsn = migrated_database.replace("postgresql+asyncpg://", "postgresql://", 1)
        async with AsyncPostgresSaver.from_conn_string(dsn) as fresh_checkpointer:
            await fresh_checkpointer.setup()
            fresh_ctx = {
                "redis": redis,
                "session_factory": node_deps.session_factory,
                "node_deps": node_deps,
                # The worker always builds both; a task admitted to the
                # offline profile must run there even on a live deployment.
                "offline_deps": node_deps,
                "checkpointer": fresh_checkpointer,
                "settings": node_deps.settings,
            }
            result = await resume_task(fresh_ctx, task_id, {"action": ApprovalAction.APPROVE.value})

        assert result == "resumed"
        # Resuming must continue, not restart: no node re-ran.
        assert await steps_for(node_deps, task_id) == steps_before
        assert len(await reports_for(node_deps, task_id)) == 1

    async def test_a_revision_after_restart_produces_exactly_one_new_version(
        self,
        node_deps: NodeDeps,
        worker_ctx: dict[str, Any],
        redis: Redis,
        migrated_database: str,
    ) -> None:
        task_id = await create_task_row(node_deps, question=QUESTION)
        await run_task(worker_ctx, task_id)

        dsn = migrated_database.replace("postgresql+asyncpg://", "postgresql://", 1)
        async with AsyncPostgresSaver.from_conn_string(dsn) as fresh_checkpointer:
            await fresh_checkpointer.setup()
            fresh_ctx = {
                "redis": redis,
                "session_factory": node_deps.session_factory,
                "node_deps": node_deps,
                # The worker always builds both; a task admitted to the
                # offline profile must run there even on a live deployment.
                "offline_deps": node_deps,
                "checkpointer": fresh_checkpointer,
                "settings": node_deps.settings,
            }
            await resume_task(
                fresh_ctx,
                task_id,
                {
                    "action": ApprovalAction.REQUEST_REVISION.value,
                    "feedback": "Please expand the licensing section.",
                },
            )

        reports = await reports_for(node_deps, task_id)
        assert [r.version for r in reports] == [1, 2]


class TestStatusSettlement:
    """A revision parks at the gate again, so the status must follow.

    Regression test: resume_task originally returned without reconciling the
    task's status, so a revised task stayed `running` forever even though its
    report was ready to review. The Docker restart test caught it.
    """

    async def test_a_revision_returns_the_task_to_awaiting_approval(
        self, node_deps: NodeDeps, worker_ctx: dict[str, Any]
    ) -> None:
        task_id = await create_task_row(node_deps, question=QUESTION)
        await run_task(worker_ctx, task_id)

        # The API moves the task to RUNNING before enqueuing the resume.
        async with node_deps.session_factory() as session, session.begin():
            task = await session.get(ResearchTask, uuid.UUID(task_id))
            assert task is not None
            task.status = TaskStatus.RUNNING

        await resume_task(
            worker_ctx,
            task_id,
            {
                "action": ApprovalAction.REQUEST_REVISION.value,
                "feedback": "Please expand the licensing section.",
            },
        )

        async with node_deps.session_factory() as session:
            task = await session.get(ResearchTask, uuid.UUID(task_id))
        assert task is not None
        assert task.status is TaskStatus.AWAITING_APPROVAL
        assert [r.version for r in await reports_for(node_deps, task_id)] == [1, 2]

    async def test_approval_is_not_overwritten_when_the_graph_finishes(
        self, node_deps: NodeDeps, worker_ctx: dict[str, Any]
    ) -> None:
        """The API records approval before enqueuing; the worker must not
        downgrade it afterwards."""
        task_id = await create_task_row(node_deps, question=QUESTION)
        await run_task(worker_ctx, task_id)

        async with node_deps.session_factory() as session, session.begin():
            task = await session.get(ResearchTask, uuid.UUID(task_id))
            assert task is not None
            task.status = TaskStatus.APPROVED

        await resume_task(worker_ctx, task_id, {"action": ApprovalAction.APPROVE.value})

        async with node_deps.session_factory() as session:
            task = await session.get(ResearchTask, uuid.UUID(task_id))
        assert task is not None
        assert task.status is TaskStatus.APPROVED


class TestOrphanRecovery:
    """A worker killed with SIGKILL never releases its lock or finishes its
    task. Without a sweep the task sits in `running` forever: arq's retry is
    refused by the still-held lock, and nothing else ever looks at it.
    """

    async def _stall(self, deps: NodeDeps, task_id: str, status: TaskStatus) -> None:
        """Make a task look abandoned: wrong status, old enough to adopt."""
        async with deps.session_factory() as session, session.begin():
            task = await session.get(ResearchTask, uuid.UUID(task_id))
            assert task is not None
            task.status = status
            task.updated_at = datetime.now(UTC) - timedelta(seconds=ORPHAN_GRACE_SECONDS + 60)

    @pytest.mark.parametrize("status", [TaskStatus.RUNNING, TaskStatus.QUEUED])
    async def test_a_stalled_task_with_no_lock_is_requeued(
        self, node_deps: NodeDeps, worker_ctx: dict[str, Any], status: TaskStatus
    ) -> None:
        task_id = await create_task_row(node_deps, question=QUESTION)
        await self._stall(node_deps, task_id, status)

        requeued = await recover_orphaned_tasks(worker_ctx)

        assert requeued >= 1
        assert worker_ctx["redis"].enqueued, "the task should have been put back on the queue"
        assert task_id in {job[1] for job in worker_ctx["redis"].enqueued}

    async def test_a_task_a_live_worker_holds_is_left_alone(
        self, node_deps: NodeDeps, worker_ctx: dict[str, Any], redis: Redis
    ) -> None:
        """Adopting a task another worker is actively running would create a
        second writer -- exactly what the lock exists to prevent."""
        task_id = await create_task_row(node_deps, question=QUESTION)
        await self._stall(node_deps, task_id, TaskStatus.RUNNING)

        async with single_flight(redis, f"task:{task_id}", ttl=60):
            await recover_orphaned_tasks(worker_ctx)

        assert task_id not in {job[1] for job in worker_ctx["redis"].enqueued}

    async def test_a_recently_updated_task_is_not_adopted(
        self, node_deps: NodeDeps, worker_ctx: dict[str, Any]
    ) -> None:
        """The grace period stops a freshly-enqueued task being stolen before
        its worker has claimed it."""
        task_id = await create_task_row(node_deps, question=QUESTION)
        # Left at its natural, very recent updated_at.
        await recover_orphaned_tasks(worker_ctx)
        assert task_id not in {job[1] for job in worker_ctx["redis"].enqueued}

    @pytest.mark.parametrize(
        "status", [TaskStatus.AWAITING_APPROVAL, TaskStatus.APPROVED, TaskStatus.FAILED]
    )
    async def test_tasks_not_in_flight_are_never_adopted(
        self, node_deps: NodeDeps, worker_ctx: dict[str, Any], status: TaskStatus
    ) -> None:
        """A task waiting for a human is not stalled; re-running it would
        discard the report under review."""
        task_id = await create_task_row(node_deps, question=QUESTION)
        await self._stall(node_deps, task_id, status)

        await recover_orphaned_tasks(worker_ctx)

        assert task_id not in {job[1] for job in worker_ctx["redis"].enqueued}


class TestNodeIdempotency:
    async def test_rerunning_the_draft_node_rewrites_its_version(self, node_deps: NodeDeps) -> None:
        """Version is derived from the revision round, not a counter, so a
        re-run targets the same version instead of inserting a duplicate."""
        from app.graph.nodes.draft import draft_node

        task_id = await create_task_row(node_deps, question=QUESTION)
        state: Any = {
            "task_id": task_id,
            "question": QUESTION,
            "subtasks": ["a"],
            "source_ids": [],
            "verdicts": [
                {
                    "claim_text": "A claim with no supporting source.",
                    "support_status": "unsupported",
                    "source_ids": [],
                    "rationale": "none",
                }
            ],
            "revision_round": 0,
        }

        await draft_node(state, node_deps)
        await draft_node(state, node_deps)

        reports = await reports_for(node_deps, task_id)
        assert len(reports) == 1
        assert reports[0].version == 1

        async with node_deps.session_factory() as session:
            claims = int(
                await session.scalar(
                    select(func.count())
                    .select_from(ReportClaim)
                    .where(ReportClaim.report_id == reports[0].id)
                )
                or 0
            )
        assert claims == 1, "claims must be rebuilt, not duplicated"

    async def test_a_failing_node_records_a_failed_step(self, node_deps: NodeDeps) -> None:
        """A failure must be visible in the timeline, not silently absent."""
        from app.adapters.llm.stub import StubLLMClient
        from app.graph.nodes.plan import plan_node

        task_id = await create_task_row(node_deps, question=QUESTION)
        broken = NodeDeps(
            settings=node_deps.settings,
            session_factory=node_deps.session_factory,
            llm=StubLLMClient({}),  # no canned response registered
            search=node_deps.search,
        )

        with pytest.raises(Exception, match="No stub response"):
            await plan_node({"task_id": task_id, "question": QUESTION}, broken)

        async with node_deps.session_factory() as session:
            step = await session.scalar(
                select(WorkflowStep).where(WorkflowStep.task_id == uuid.UUID(task_id))
            )
        assert step is not None
        assert step.status is StepStatus.FAILED
        assert step.error is not None
