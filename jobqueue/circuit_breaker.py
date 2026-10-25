from __future__ import annotations

import logging

from redis.asyncio import Redis

from config.breaker import BreakerSettings
from domain.value_objects.ids import ProviderId
from services.ports.circuit_breaker import CircuitState

logger = logging.getLogger(__name__)


class RedisCircuitBreaker:
    """
    Implements services.ports.circuit_breaker.CircuitBreaker.

    Keys per provider:
      fail   consecutive-failure counter; each failure refreshes its TTL, so
             failures only count as "consecutive" while they keep coming
      open   present for the cooldown after the threshold is reached
      probe  short lease held by the single message testing a half-open provider

    Open ends by TTL expiry, leaving the failure counter above the threshold:
    that is the half-open state. A probe success clears everything; a probe
    failure re-opens the circuit for another cooldown.
    """

    def __init__(self, redis: Redis, settings: BreakerSettings) -> None:
        self._redis = redis
        self._s = settings

    @staticmethod
    def _key(kind: str, provider_id: ProviderId) -> str:
        return f"pulse:cb:{kind}:{provider_id}"

    async def _failures(self, provider_id: ProviderId) -> int:
        raw = await self._redis.get(self._key("fail", provider_id))
        return int(raw) if raw else 0

    async def state(self, provider_id: ProviderId) -> CircuitState:
        if await self._redis.exists(self._key("open", provider_id)):
            return "open"
        if await self._failures(provider_id) >= self._s.failure_threshold:
            return "half_open"
        return "closed"

    async def allow(self, provider_id: ProviderId) -> bool:
        if not self._s.enabled:
            return True
        state = await self.state(provider_id)
        if state == "closed":
            return True
        if state == "open":
            return False
        # Half-open: exactly one probe at a time.
        return bool(
            await self._redis.set(
                self._key("probe", provider_id), "1", nx=True, ex=self._s.probe_lease_seconds
            )
        )

    async def record_success(self, provider_id: ProviderId) -> None:
        if not self._s.enabled:
            return
        was = await self.state(provider_id)
        await self._redis.delete(
            self._key("fail", provider_id),
            self._key("open", provider_id),
            self._key("probe", provider_id),
        )
        if was != "closed":
            logger.info("Provider %s recovered; circuit closed", provider_id)

    async def record_failure(self, provider_id: ProviderId) -> None:
        if not self._s.enabled:
            return
        fail_key = self._key("fail", provider_id)
        count = await self._redis.incr(fail_key)
        await self._redis.expire(fail_key, self._s.window_seconds)
        if count >= self._s.failure_threshold:
            await self._redis.set(self._key("open", provider_id), "1", ex=self._s.cooldown_seconds)
            await self._redis.delete(self._key("probe", provider_id))
            if count == self._s.failure_threshold:
                logger.warning(
                    "Provider %s failed %d times in a row; circuit open for %ds",
                    provider_id, count, self._s.cooldown_seconds,
                )
