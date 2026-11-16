"""API behaviour.

Exercises the real application through ASGI: routing, the error envelope,
session scoping, idempotency and that submitting a task enqueues exactly one
job. The queue and LLM runtime are fakes, so no broker or provider is needed.

Marked ``integration``: these use the database.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.llm.registry import STUB_MODEL_ID, LLMRuntime
from app.adapters.llm.stub import StubLLMClient
from app.api.deps import (
    get_db,
    get_llm_runtime,
    get_llm_runtime_if_ready,
    get_queue,
    get_queue_if_ready,
)
from app.core.config import get_settings
from app.core.security import SESSION_COOKIE_NAME, new_session_id, sign_session_id
from app.db.models import (
    ApprovalEvent,
    ClaimSource,
    Report,
    ReportClaim,
    ResearchTask,
    Source,
    WorkflowStep,
)
from app.domain.enums import (
    ApprovalAction,
    LLMProvider,
    StepStatus,
    SupportStatus,
    TaskStatus,
)
from app.domain.nodes import LLM_NODES, NodeName
from app.main import create_app
from tests.conftest import make_settings

pytestmark = pytest.mark.integration

SESSION_SECRET = "0" * 64


class FakeQueue:
    """Records enqueued work so tests can assert on it."""

    def __init__(self) -> None:
        self.runs: list[str] = []
        self.resumes: list[tuple[str, dict[str, Any]]] = []

    async def enqueue_run(self, task_id: str) -> None:
        self.runs.append(task_id)

    async def enqueue_resume(self, task_id: str, decision: dict[str, Any]) -> None:
        self.resumes.append((task_id, decision))

    async def ping(self) -> bool:
        return True


def stub_runtime() -> LLMRuntime:
    return LLMRuntime(
        client=StubLLMClient({}),
        provider=LLMProvider.STUB,
        models=dict.fromkeys(sorted(LLM_NODES), STUB_MODEL_ID),
        catalogue_source="stub",
    )


@pytest.fixture
async def api(db_session: AsyncSession) -> AsyncIterator[tuple[AsyncClient, FakeQueue]]:
    """The real app, with the queue and provider replaced by fakes."""
    settings = make_settings(session_secret=SESSION_SECRET)
    get_settings.cache_clear()
    app = create_app()
    queue = FakeQueue()

    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_queue] = lambda: queue
    app.dependency_overrides[get_llm_runtime] = stub_runtime
    app.dependency_overrides[get_queue_if_ready] = lambda: queue
    app.dependency_overrides[get_llm_runtime_if_ready] = stub_runtime

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, queue


@pytest.fixture
async def session_client(
    api: tuple[AsyncClient, FakeQueue],
) -> tuple[AsyncClient, FakeQueue, str]:
    """A client carrying a fixed, validly signed session cookie."""
    client, queue = api
    session_id = new_session_id()
    client.cookies.set(SESSION_COOKIE_NAME, sign_session_id(session_id, SESSION_SECRET))
    return client, queue, session_id


QUESTION = "Compare three open-source workflow orchestration frameworks."


class TestHealth:
    async def test_liveness_is_cheap_and_unconditional(
        self, api: tuple[AsyncClient, FakeQueue]
    ) -> None:
        client, _ = api
        response = await client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    async def test_readiness_reports_each_component(
        self, api: tuple[AsyncClient, FakeQueue]
    ) -> None:
        client, _ = api
        response = await client.get("/ready")
        assert response.status_code == 200
        body = response.json()
        assert body["ready"] is True
        assert {c["name"] for c in body["components"]} == {"database", "queue", "llm"}


class TestMeta:
    async def test_meta_reports_the_resolved_model_for_every_node(
        self, api: tuple[AsyncClient, FakeQueue]
    ) -> None:
        """An M1 acceptance criterion: the resolved configuration is visible."""
        client, _ = api
        body = (await client.get("/api/meta")).json()
        assert {m["node"] for m in body["models"]} == {n.value for n in LLM_NODES}
        assert NodeName.RESEARCH.value not in {m["node"] for m in body["models"]}

    async def test_meta_declares_offline_mode_rather_than_implying_live_ai(
        self, api: tuple[AsyncClient, FakeQueue]
    ) -> None:
        client, _ = api
        body = (await client.get("/api/meta")).json()
        assert body["offline_mode"] is True
        assert body["llm_provider"] == "stub"
        assert body["search_provider"] == "fixture"
        assert body["require_structured_output"] is True


class TestTaskCreation:
    async def test_submitting_a_task_returns_202_and_queues_one_job(
        self, session_client: tuple[AsyncClient, FakeQueue, str]
    ) -> None:
        """202, not 200: the research has not run yet."""
        client, queue, _ = session_client
        response = await client.post("/api/tasks", json={"question": QUESTION})
        assert response.status_code == 202
        body = response.json()
        assert body["status"] == TaskStatus.QUEUED.value
        assert response.headers["Location"] == f"/api/tasks/{body['id']}"
        assert queue.runs == [body["id"]]

    async def test_provenance_and_budget_are_captured_at_creation(
        self, session_client: tuple[AsyncClient, FakeQueue, str]
    ) -> None:
        """A later config change must not rewrite what a running task was
        permitted to spend, nor what produced its report."""
        client, _, _ = session_client
        body = (await client.post("/api/tasks", json={"question": QUESTION})).json()
        assert body["llm_provider"] == "stub"
        assert body["search_mode"] == "fixture"
        assert body["max_tokens_budget"] > 0
        assert body["max_calls_budget"] > 0

    async def test_a_repeated_idempotency_key_does_not_create_a_duplicate(
        self, session_client: tuple[AsyncClient, FakeQueue, str]
    ) -> None:
        client, queue, _ = session_client
        headers = {"Idempotency-Key": "retry-1"}
        first = await client.post("/api/tasks", json={"question": QUESTION}, headers=headers)
        second = await client.post("/api/tasks", json={"question": QUESTION}, headers=headers)
        assert first.json()["id"] == second.json()["id"]
        assert len(queue.runs) == 1

    @pytest.mark.parametrize(
        "payload",
        [
            {"question": "too short"},
            {"question": ""},
            {},
            {"question": QUESTION, "unexpected": "field"},
        ],
    )
    async def test_invalid_input_is_rejected_in_the_standard_envelope(
        self, session_client: tuple[AsyncClient, FakeQueue, str], payload: dict[str, Any]
    ) -> None:
        client, queue, _ = session_client
        response = await client.post("/api/tasks", json=payload)
        assert response.status_code == 422
        error = response.json()["error"]
        assert error["code"] == "validation_error"
        assert "fields" in error["details"]
        assert queue.runs == []

    async def test_a_session_cookie_is_issued_on_first_contact(
        self, api: tuple[AsyncClient, FakeQueue]
    ) -> None:
        client, _ = api
        response = await client.post("/api/tasks", json={"question": QUESTION})
        assert response.status_code == 202
        assert SESSION_COOKIE_NAME in response.cookies


class TestTaskReads:
    async def test_a_task_is_readable_by_its_own_session(
        self, session_client: tuple[AsyncClient, FakeQueue, str]
    ) -> None:
        client, _, _ = session_client
        task_id = (await client.post("/api/tasks", json={"question": QUESTION})).json()["id"]
        response = await client.get(f"/api/tasks/{task_id}")
        assert response.status_code == 200
        assert response.json()["question"] == QUESTION

    async def test_another_session_cannot_read_it(
        self, session_client: tuple[AsyncClient, FakeQueue, str]
    ) -> None:
        """Reported as missing, not forbidden: the endpoint must not confirm
        that an id exists."""
        client, _, _ = session_client
        task_id = (await client.post("/api/tasks", json={"question": QUESTION})).json()["id"]
        client.cookies.set(SESSION_COOKIE_NAME, sign_session_id(new_session_id(), SESSION_SECRET))
        response = await client.get(f"/api/tasks/{task_id}")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"

    async def test_an_unknown_task_is_a_404_envelope(
        self, session_client: tuple[AsyncClient, FakeQueue, str]
    ) -> None:
        client, _, _ = session_client
        response = await client.get(f"/api/tasks/{uuid.uuid4()}")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"

    async def test_a_malformed_id_is_a_validation_error(
        self, session_client: tuple[AsyncClient, FakeQueue, str]
    ) -> None:
        client, _, _ = session_client
        assert (await client.get("/api/tasks/not-a-uuid")).status_code == 422

    async def test_the_list_only_contains_this_session_s_tasks(
        self, session_client: tuple[AsyncClient, FakeQueue, str]
    ) -> None:
        client, _, _ = session_client
        await client.post("/api/tasks", json={"question": QUESTION})
        mine = (await client.get("/api/tasks")).json()
        assert len(mine["items"]) == 1

        client.cookies.set(SESSION_COOKIE_NAME, sign_session_id(new_session_id(), SESSION_SECRET))
        theirs = (await client.get("/api/tasks")).json()
        assert theirs["items"] == []

    async def test_the_timeline_shows_every_attempt_including_failures(
        self, session_client: tuple[AsyncClient, FakeQueue, str], db_session: AsyncSession
    ) -> None:
        client, _, _ = session_client
        task_id = (await client.post("/api/tasks", json={"question": QUESTION})).json()["id"]
        db_session.add_all(
            [
                WorkflowStep(
                    task_id=uuid.UUID(task_id),
                    node=NodeName.PLAN,
                    status=StepStatus.FAILED,
                    attempt=1,
                ),
                WorkflowStep(
                    task_id=uuid.UUID(task_id),
                    node=NodeName.PLAN,
                    status=StepStatus.SUCCEEDED,
                    attempt=2,
                ),
            ]
        )
        await db_session.flush()
        steps = (await client.get(f"/api/tasks/{task_id}/steps")).json()
        assert [s["attempt"] for s in steps] == [1, 2]
        assert [s["status"] for s in steps] == ["failed", "succeeded"]


class TestApproval:
    async def test_a_task_awaiting_approval_can_be_approved(
        self, session_client: tuple[AsyncClient, FakeQueue, str], db_session: AsyncSession
    ) -> None:
        client, queue, _ = session_client
        task_id = (await client.post("/api/tasks", json={"question": QUESTION})).json()["id"]

        task = await db_session.get(ResearchTask, uuid.UUID(task_id))
        assert task is not None
        task.status = TaskStatus.AWAITING_APPROVAL
        await db_session.flush()

        response = await client.post(f"/api/tasks/{task_id}/approve", json={"feedback": "good"})
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == TaskStatus.APPROVED.value
        assert body["action"] == "approve"
        # The graph is resumed by the worker, not inside the request.
        assert queue.resumes and queue.resumes[0][0] == task_id

    @pytest.mark.parametrize("status", [TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.FAILED])
    async def test_approving_a_task_not_at_the_gate_is_a_conflict(
        self,
        session_client: tuple[AsyncClient, FakeQueue, str],
        db_session: AsyncSession,
        status: TaskStatus,
    ) -> None:
        """Approving anything else would fabricate a decision about a report
        that does not exist yet."""
        client, queue, _ = session_client
        task_id = (await client.post("/api/tasks", json={"question": QUESTION})).json()["id"]
        task = await db_session.get(ResearchTask, uuid.UUID(task_id))
        assert task is not None
        task.status = status
        await db_session.flush()

        response = await client.post(f"/api/tasks/{task_id}/approve", json={})
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "conflict"
        assert queue.resumes == []

    async def test_approval_is_recorded_against_a_report_version(
        self, session_client: tuple[AsyncClient, FakeQueue, str], db_session: AsyncSession
    ) -> None:
        client, _, _ = session_client
        task_id = (await client.post("/api/tasks", json={"question": QUESTION})).json()["id"]
        task = await db_session.get(ResearchTask, uuid.UUID(task_id))
        assert task is not None
        task.status = TaskStatus.AWAITING_APPROVAL
        await db_session.flush()

        body = (await client.post(f"/api/tasks/{task_id}/approve", json={})).json()
        assert body["report_version"] >= 1

        stored = await db_session.scalar(
            select(ResearchTask).where(ResearchTask.id == uuid.UUID(task_id))
        )
        assert stored is not None
        assert stored.status is TaskStatus.APPROVED


class TestEnvelopeAndCorrelation:
    async def test_the_correlation_id_is_echoed_for_log_tracing(
        self, api: tuple[AsyncClient, FakeQueue]
    ) -> None:
        client, _ = api
        response = await client.get("/health", headers={"X-Correlation-ID": "trace-me-123"})
        assert response.headers["X-Correlation-ID"] == "trace-me-123"

    async def test_an_untrusted_correlation_header_is_replaced(
        self, api: tuple[AsyncClient, FakeQueue]
    ) -> None:
        """An inbound header flows into every log line, so it is sanitised."""
        client, _ = api
        response = await client.get("/health", headers={"X-Correlation-ID": "x" * 500})
        assert len(response.headers["X-Correlation-ID"]) <= 64

    async def test_a_generated_correlation_id_is_always_present(
        self, api: tuple[AsyncClient, FakeQueue]
    ) -> None:
        client, _ = api
        assert (await client.get("/health")).headers["X-Correlation-ID"]


class TestExportGate:
    """Approval gates export. This is the product's central promise."""

    async def _task_with_report(
        self, client: AsyncClient, db_session: AsyncSession, status: TaskStatus
    ) -> str:
        task_id = (await client.post("/api/tasks", json={"question": QUESTION})).json()["id"]
        task = await db_session.get(ResearchTask, uuid.UUID(task_id))
        assert task is not None
        task.status = status
        db_session.add(
            Report(
                task_id=uuid.UUID(task_id),
                version=1,
                content_markdown="# Report\n\nBody.",
            )
        )
        await db_session.flush()
        return task_id

    @pytest.mark.parametrize(
        "status",
        [TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.AWAITING_APPROVAL, TaskStatus.FAILED],
    )
    async def test_export_is_refused_until_approved(
        self,
        session_client: tuple[AsyncClient, FakeQueue, str],
        db_session: AsyncSession,
        status: TaskStatus,
    ) -> None:
        client, _, _ = session_client
        task_id = await self._task_with_report(client, db_session, status)

        response = await client.get(f"/api/tasks/{task_id}/export")
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "approval_required"

    async def test_an_approved_report_exports_as_markdown(
        self, session_client: tuple[AsyncClient, FakeQueue, str], db_session: AsyncSession
    ) -> None:
        client, _, _ = session_client
        task_id = await self._task_with_report(client, db_session, TaskStatus.APPROVED)

        response = await client.get(f"/api/tasks/{task_id}/export")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/markdown")
        assert "attachment" in response.headers["content-disposition"]
        assert response.text.startswith("# Report")

    async def test_another_session_cannot_export(
        self, session_client: tuple[AsyncClient, FakeQueue, str], db_session: AsyncSession
    ) -> None:
        client, _, _ = session_client
        task_id = await self._task_with_report(client, db_session, TaskStatus.APPROVED)
        client.cookies.set(SESSION_COOKIE_NAME, sign_session_id(new_session_id(), SESSION_SECRET))
        assert (await client.get(f"/api/tasks/{task_id}/export")).status_code == 404


class TestRevisionEndpoint:
    async def test_a_revision_is_recorded_and_queued(
        self, session_client: tuple[AsyncClient, FakeQueue, str], db_session: AsyncSession
    ) -> None:
        client, queue, _ = session_client
        task_id = (await client.post("/api/tasks", json={"question": QUESTION})).json()["id"]
        task = await db_session.get(ResearchTask, uuid.UUID(task_id))
        assert task is not None
        task.status = TaskStatus.AWAITING_APPROVAL
        await db_session.flush()

        response = await client.post(
            f"/api/tasks/{task_id}/request-revision",
            json={"feedback": "Please expand the licensing section."},
        )

        assert response.status_code == 200
        assert response.json()["action"] == "request_revision"
        assert response.json()["status"] == TaskStatus.RUNNING.value
        assert queue.resumes and queue.resumes[-1][1]["action"] == "request_revision"

    async def test_feedback_is_required(
        self, session_client: tuple[AsyncClient, FakeQueue, str], db_session: AsyncSession
    ) -> None:
        """A revision with no explanation gives the workflow nothing to act on."""
        client, _, _ = session_client
        task_id = (await client.post("/api/tasks", json={"question": QUESTION})).json()["id"]
        task = await db_session.get(ResearchTask, uuid.UUID(task_id))
        assert task is not None
        task.status = TaskStatus.AWAITING_APPROVAL
        await db_session.flush()

        response = await client.post(
            f"/api/tasks/{task_id}/request-revision", json={"feedback": ""}
        )
        assert response.status_code == 422

    async def test_a_revision_outside_the_gate_is_a_conflict(
        self, session_client: tuple[AsyncClient, FakeQueue, str]
    ) -> None:
        client, queue, _ = session_client
        task_id = (await client.post("/api/tasks", json={"question": QUESTION})).json()["id"]

        response = await client.post(
            f"/api/tasks/{task_id}/request-revision", json={"feedback": "Change it."}
        )
        assert response.status_code == 409
        assert queue.resumes == []

    async def test_the_revision_cap_is_enforced(
        self, session_client: tuple[AsyncClient, FakeQueue, str], db_session: AsyncSession
    ) -> None:
        """Each revision re-plans and re-retrieves, so the loop is bounded."""
        client, _, _ = session_client
        task_id = (await client.post("/api/tasks", json={"question": QUESTION})).json()["id"]
        task = await db_session.get(ResearchTask, uuid.UUID(task_id))
        assert task is not None
        task.status = TaskStatus.AWAITING_APPROVAL
        for _ in range(3):  # the configured cap
            db_session.add(
                ApprovalEvent(
                    task_id=uuid.UUID(task_id),
                    report_version=1,
                    session_id=task.session_id,
                    action=ApprovalAction.REQUEST_REVISION,
                    feedback="again",
                )
            )
        await db_session.flush()

        response = await client.post(
            f"/api/tasks/{task_id}/request-revision", json={"feedback": "Once more."}
        )
        assert response.status_code == 409
        assert "limit" in response.json()["error"]["message"].lower()


class TestSourcesAndClaims:
    async def test_sources_are_returned_with_their_model_labels(
        self, session_client: tuple[AsyncClient, FakeQueue, str], db_session: AsyncSession
    ) -> None:
        client, _, _ = session_client
        task_id = (await client.post("/api/tasks", json={"question": QUESTION})).json()["id"]
        for index in range(2):
            db_session.add(
                Source(
                    task_id=uuid.UUID(task_id),
                    subtask_index=index,
                    url=f"https://fixtures.agentflow.test/doc-{index}",
                    url_sha256=f"{index}" * 64,
                    title=f"Doc {index}",
                    excerpt="text",
                    provider="fixture",
                    rank=0,
                    is_fixture=True,
                )
            )
        await db_session.flush()

        body = (await client.get(f"/api/tasks/{task_id}/sources")).json()
        assert [s["label"] for s in body] == ["S1", "S2"]
        assert all(s["is_fixture"] for s in body)

    async def test_claims_report_support_and_evidence(
        self, session_client: tuple[AsyncClient, FakeQueue, str], db_session: AsyncSession
    ) -> None:
        client, _, _ = session_client
        task_id = (await client.post("/api/tasks", json={"question": QUESTION})).json()["id"]
        source = Source(
            task_id=uuid.UUID(task_id),
            subtask_index=0,
            url="https://fixtures.agentflow.test/evidence",
            url_sha256="a" * 64,
            title="Evidence",
            excerpt="text",
            provider="fixture",
            rank=0,
            is_fixture=True,
        )
        report = Report(task_id=uuid.UUID(task_id), version=1, content_markdown="# r")
        db_session.add_all([source, report])
        await db_session.flush()

        supported = ReportClaim(
            report_id=report.id,
            ordinal=0,
            claim_text="A claim a source states.",
            support_status=SupportStatus.SUPPORTED,
        )
        unsupported = ReportClaim(
            report_id=report.id,
            ordinal=1,
            claim_text="A claim nothing states.",
            support_status=SupportStatus.UNSUPPORTED,
        )
        db_session.add_all([supported, unsupported])
        await db_session.flush()
        db_session.add(ClaimSource(claim_id=supported.id, source_id=source.id))
        await db_session.flush()

        body = (await client.get(f"/api/tasks/{task_id}/claims")).json()
        assert [c["support_status"] for c in body] == ["supported", "unsupported"]
        assert body[0]["source_labels"] == ["S1"]
        assert body[1]["source_labels"] == []

    async def test_a_report_can_be_read_before_approval(
        self, session_client: tuple[AsyncClient, FakeQueue, str], db_session: AsyncSession
    ) -> None:
        """Approval gates export, not inspection: a reviewer must be able to
        read what they are approving."""
        client, _, _ = session_client
        task_id = (await client.post("/api/tasks", json={"question": QUESTION})).json()["id"]
        task = await db_session.get(ResearchTask, uuid.UUID(task_id))
        assert task is not None
        task.status = TaskStatus.AWAITING_APPROVAL
        db_session.add(Report(task_id=uuid.UUID(task_id), version=1, content_markdown="# Draft"))
        await db_session.flush()

        response = await client.get(f"/api/tasks/{task_id}/report")
        assert response.status_code == 200
        assert response.json()["version"] == 1

    async def test_a_missing_report_is_a_404(
        self, session_client: tuple[AsyncClient, FakeQueue, str]
    ) -> None:
        client, _, _ = session_client
        task_id = (await client.post("/api/tasks", json={"question": QUESTION})).json()["id"]
        assert (await client.get(f"/api/tasks/{task_id}/report")).status_code == 404
