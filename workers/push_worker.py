"""
Push + in-app delivery worker (both device-facing; in-app falls back to push).

Run with:  uv run arq workers.push_worker.WorkerSettings
"""
from __future__ import annotations

from typing import Any

from config.loader import get_settings
from domain.value_objects.ids import DeliveryAttemptId, TenantId
from jobqueue.producer import PUSH_QUEUE, arq_redis_settings
from services.delivery_service import DeliveryService
from workers.context import build_worker_context, teardown_worker_context


async def _deliver(ctx: dict[str, Any], payload: dict[str, Any]) -> str:
    service: DeliveryService = ctx["delivery_service"]
    return await service.deliver(
        TenantId.from_str(payload["tenant_id"]),
        DeliveryAttemptId.from_str(payload["attempt_id"]),
    )


async def deliver_push(ctx: dict[str, Any], payload: dict[str, Any]) -> str:
    return await _deliver(ctx, payload)


async def deliver_in_app(ctx: dict[str, Any], payload: dict[str, Any]) -> str:
    return await _deliver(ctx, payload)


class WorkerSettings:
    functions = [deliver_push, deliver_in_app]
    queue_name = PUSH_QUEUE
    on_startup = build_worker_context
    on_shutdown = teardown_worker_context
    redis_settings = arq_redis_settings(get_settings().redis)
    max_tries = 1
