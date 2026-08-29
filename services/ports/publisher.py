from __future__ import annotations

from typing import Any, Protocol


class JobPublisher(Protocol):
    """Port for handing delivery jobs to the queue (implemented over arq/Redis)."""

    async def enqueue(
        self,
        topic: str,
        payload: dict[str, Any],
        *,
        job_id: str | None = None,
        defer_by_seconds: float | None = None,
    ) -> None: ...
