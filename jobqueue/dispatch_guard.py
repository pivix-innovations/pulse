from __future__ import annotations

from redis.asyncio import Redis


class RedisDispatchGuard:
    """Implements services.ports.dispatch_guard.DispatchGuard with SET NX."""

    def __init__(self, redis: Redis, ttl_seconds: int = 24 * 3600) -> None:
        self._redis = redis
        self._ttl = ttl_seconds

    async def acquire(self, key: str) -> bool:
        return bool(await self._redis.set(f"pulse:dispatch:{key}", "1", nx=True, ex=self._ttl))

    async def release(self, key: str) -> None:
        await self._redis.delete(f"pulse:dispatch:{key}")
