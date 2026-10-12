"""
Email delivery worker.

Run with:  uv run arq workers.email_worker.WorkerSettings
(The outbox relay and reconciliation sweep run in workers.scheduler_worker.)
"""
from __future__ import annotations

from typing import Any

from config.loader import get_settings
from domain.value_objects.ids import DeliveryAttemptId, TenantId
from jobqueue.producer import EMAIL_QUEUE, arq_redis_settings
from services.delivery_service import DeliveryService
from workers.context import build_worker_context, teardown_worker_context


async def deliver_email(ctx: dict[str, Any], payload: dict[str, Any]) -> str:
    service: DeliveryService = ctx["delivery_service"]
    return await service.deliver(
        TenantId.from_str(payload["tenant_id"]),
        DeliveryAttemptId.from_str(payload["attempt_id"]),
    )


class WorkerSettings:
    functions = [deliver_email]
    queue_name = EMAIL_QUEUE
    on_startup = build_worker_context
    on_shutdown = teardown_worker_context
    redis_settings = arq_redis_settings(get_settings().redis)
    max_tries = 1  # retries are app-level (DeliveryService backoff)
