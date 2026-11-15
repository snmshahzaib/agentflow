"""The background-queue port.

The API never executes a research task; it enqueues one. Keeping that behind a
port means the service layer has no opinion about arq, and tests can assert
"this enqueued exactly one job" without a broker.
"""

from __future__ import annotations

from typing import Any, Protocol


class TaskQueue(Protocol):
    """Outbound port for handing work to the worker process."""

    async def enqueue_run(self, task_id: str) -> None:
        """Queue the first execution of a research task."""
        ...

    async def enqueue_resume(self, task_id: str, decision: dict[str, Any]) -> None:
        """Queue continuation of a task paused at the approval interrupt."""
        ...

    async def ping(self) -> bool:
        """Whether the broker is reachable. Used by the readiness probe."""
        ...
