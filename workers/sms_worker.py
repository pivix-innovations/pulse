"""
SMS delivery worker.

Run with:  uv run arq workers.sms_worker.WorkerSettings
"""
from __future__ import annotations

from typing import Any

from config.loader import get_settings
from domain.value_objects.ids import DeliveryAttemptId, TenantId
from jobqueue.producer import SMS_QUEUE, arq_redis_settings
from services.delivery_service import DeliveryService
from workers.context import build_worker_context, teardown_worker_context


async def deliver_sms(ctx: dict[str, Any], payload: dict[str, Any]) -> str:
    service: DeliveryService = ctx["delivery_service"]
    return await service.deliver(
        TenantId.from_str(payload["tenant_id"]),
        DeliveryAttemptId.from_str(payload["attempt_id"]),
        traceparent=payload.get("traceparent"),
    )


async def on_startup(ctx: dict[str, Any]) -> None:
    await build_worker_context(ctx, "sms")


class WorkerSettings:
    functions = [deliver_sms]
    queue_name = SMS_QUEUE
    on_startup = on_startup
    on_shutdown = teardown_worker_context
    redis_settings = arq_redis_settings(get_settings().redis)
    max_tries = 1
