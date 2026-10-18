from __future__ import annotations

from typing import Any

from arq import ArqRedis, create_pool
from arq.connections import RedisSettings as ArqRedisSettings

from config.redis import RedisSettings

# Queue names: one arq queue per worker process so each worker only sees
# tasks it can execute.
EMAIL_QUEUE = "pulse:email"
PUSH_QUEUE = "pulse:push"
SMS_QUEUE = "pulse:sms"

# Maps outbox topics to (task name, queue). The in_app task runs in the
# push worker: both are device-facing, and in_app falls back to push.
TOPIC_ROUTES: dict[str, tuple[str, str]] = {
    "deliver.email": ("deliver_email", EMAIL_QUEUE),
    "deliver.push": ("deliver_push", PUSH_QUEUE),
    "deliver.in_app": ("deliver_in_app", PUSH_QUEUE),
    "deliver.sms": ("deliver_sms", SMS_QUEUE),
}


def arq_redis_settings(settings: RedisSettings) -> ArqRedisSettings:
    return ArqRedisSettings(
        host=settings.host,
        port=settings.port,
        database=settings.db,
        password=settings.password,
        username=settings.username,
        ssl=settings.ssl,
    )


async def create_arq_pool(settings: RedisSettings) -> ArqRedis:
    return await create_pool(arq_redis_settings(settings))


class ArqJobPublisher:
    """Implements services.ports.publisher.JobPublisher over an arq pool."""

    def __init__(self, pool: ArqRedis) -> None:
        self._pool = pool

    async def enqueue(
        self,
        topic: str,
        payload: dict[str, Any],
        *,
        job_id: str | None = None,
        defer_by_seconds: float | None = None,
    ) -> None:
        route = TOPIC_ROUTES.get(topic)
        if route is None:
            raise LookupError(f"No task registered for outbox topic '{topic}'")
        task, queue = route
        # job_id gives queue-level dedup: re-publishing the same outbox entry
        # while the job is still queued/running is a no-op.
        await self._pool.enqueue_job(
            task, payload, _job_id=job_id, _queue_name=queue, _defer_by=defer_by_seconds
        )
