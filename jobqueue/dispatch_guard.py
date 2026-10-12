from __future__ import annotations

from redis.asyncio import Redis


class RedisDispatchGuard:
    """
    Implements services.ports.dispatch_guard.DispatchGuard with SET NX.

    `acquire` takes a short lease so a worker that dies mid-send doesn't
    lock the key out for a day; `complete` extends it to the long
    dedupe window once the provider has accepted the message.
    """

    def __init__(
        self,
        redis: Redis,
        lease_seconds: int = 180,
        completed_ttl_seconds: int = 24 * 3600,
    ) -> None:
        self._redis = redis
        self._lease = lease_seconds
        self._completed_ttl = completed_ttl_seconds

    @staticmethod
    def _key(key: str) -> str:
        return f"pulse:dispatch:{key}"

    async def acquire(self, key: str) -> bool:
        return bool(await self._redis.set(self._key(key), "1", nx=True, ex=self._lease))

    async def complete(self, key: str) -> None:
        await self._redis.set(self._key(key), "1", ex=self._completed_ttl)

    async def release(self, key: str) -> None:
        await self._redis.delete(self._key(key))
