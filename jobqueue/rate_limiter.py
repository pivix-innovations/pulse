from __future__ import annotations

from datetime import datetime, timedelta, timezone

from redis.asyncio import Redis

from domain.value_objects.ids import TenantId
from services.ports.rate_limiter import Decision, Limits

# Token bucket, atomically. Uses Redis' own clock so API instances with
# skewed clocks can't hand a tenant extra tokens.
_BUCKET = """
local key   = KEYS[1]
local rate  = tonumber(ARGV[1])
local burst = tonumber(ARGV[2])
local t     = redis.call('TIME')
local now   = tonumber(t[1]) + tonumber(t[2]) / 1000000

local data   = redis.call('HMGET', key, 'tokens', 'ts')
local tokens = tonumber(data[1])
local ts     = tonumber(data[2])
if tokens == nil then tokens = burst; ts = now end

tokens = math.min(burst, tokens + math.max(0, now - ts) * rate)
local allowed = 0
local retry = 0
if tokens >= 1 then
  tokens = tokens - 1
  allowed = 1
else
  retry = (1 - tokens) / rate
end
redis.call('HSET', key, 'tokens', tokens, 'ts', now)
redis.call('EXPIRE', key, math.ceil(burst / rate) + 10)
return {allowed, tostring(tokens), tostring(retry)}
"""


class RedisRateLimiter:
    """Implements services.ports.rate_limiter.RateLimiter."""

    def __init__(self, redis: Redis) -> None:
        self._redis = redis
        self._bucket = redis.register_script(_BUCKET)

    @staticmethod
    def _bucket_key(tenant_id: TenantId) -> str:
        return f"pulse:rl:bucket:{tenant_id}"

    @staticmethod
    def _quota_key(tenant_id: TenantId, now: datetime | None = None) -> str:
        day = (now or datetime.now(timezone.utc)).strftime("%Y%m%d")
        return f"pulse:rl:quota:{tenant_id}:{day}"

    @staticmethod
    def _seconds_until_utc_midnight(now: datetime) -> float:
        tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        return (tomorrow - now).total_seconds()

    async def acquire(self, tenant_id: TenantId, limits: Limits) -> Decision:
        allowed, tokens, retry = await self._bucket(
            keys=[self._bucket_key(tenant_id)], args=[limits.rate_per_second, limits.burst]
        )
        tokens_left, retry_after = float(tokens), float(retry)
        if not int(allowed):
            return Decision(False, "rate", retry_after, tokens_left)

        if limits.daily_quota is None:
            return Decision(True, None, 0.0, tokens_left, None)

        now = datetime.now(timezone.utc)
        key = self._quota_key(tenant_id, now)
        used = await self._redis.incr(key)
        if used == 1:
            await self._redis.expire(key, 2 * 24 * 3600)  # outlives the day it counts
        if used > limits.daily_quota:
            await self._redis.decr(key)  # a denied request doesn't consume quota
            return Decision(False, "quota", self._seconds_until_utc_midnight(now), tokens_left, 0)
        return Decision(True, None, 0.0, tokens_left, limits.daily_quota - used)

    async def refund_quota(self, tenant_id: TenantId) -> None:
        key = self._quota_key(tenant_id)
        if await self._redis.exists(key):
            await self._redis.decr(key)

    async def used_today(self, tenant_id: TenantId) -> int:
        raw = await self._redis.get(self._quota_key(tenant_id))
        return max(0, int(raw)) if raw else 0
