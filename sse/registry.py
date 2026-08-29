from __future__ import annotations

import contextlib
from typing import AsyncIterator

from redis.asyncio import Redis

from domain.value_objects.ids import RecipientId, TenantId


def channel_name(tenant_id: TenantId, recipient_id: RecipientId) -> str:
    return f"pulse:sse:{tenant_id}:{recipient_id}"


def _conn_key(tenant_id: TenantId, recipient_id: RecipientId) -> str:
    return f"pulse:sse:conn:{tenant_id}:{recipient_id}"


class SseConnectionRegistry:
    """
    Redis-backed registry of live SSE connections. Pub/sub carries the
    events across API instances; the connection counters record who is
    online (with a TTL so crashed instances can't leak counts forever).
    """

    def __init__(self, redis: Redis, counter_ttl_seconds: int = 3600) -> None:
        self._redis = redis
        self._ttl = counter_ttl_seconds

    async def register(self, tenant_id: TenantId, recipient_id: RecipientId) -> None:
        key = _conn_key(tenant_id, recipient_id)
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.incr(key)
            pipe.expire(key, self._ttl)
            await pipe.execute()

    async def unregister(self, tenant_id: TenantId, recipient_id: RecipientId) -> None:
        key = _conn_key(tenant_id, recipient_id)
        remaining = await self._redis.decr(key)
        if remaining <= 0:
            await self._redis.delete(key)

    async def connection_count(self, tenant_id: TenantId, recipient_id: RecipientId) -> int:
        raw = await self._redis.get(_conn_key(tenant_id, recipient_id))
        return int(raw) if raw else 0

    @contextlib.asynccontextmanager
    async def subscribe(
        self, tenant_id: TenantId, recipient_id: RecipientId
    ) -> AsyncIterator["Subscription"]:
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(channel_name(tenant_id, recipient_id))
        await self.register(tenant_id, recipient_id)
        try:
            yield Subscription(pubsub)
        finally:
            await self.unregister(tenant_id, recipient_id)
            await pubsub.unsubscribe()
            await pubsub.aclose()


class Subscription:
    def __init__(self, pubsub) -> None:
        self._pubsub = pubsub

    async def get(self, timeout: float) -> str | None:
        """Next published message body, or None after `timeout` seconds."""
        message = await self._pubsub.get_message(
            ignore_subscribe_messages=True, timeout=timeout
        )
        if message is None:
            return None
        data = message["data"]
        return data.decode() if isinstance(data, bytes) else str(data)
