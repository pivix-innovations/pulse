"""The Lua token bucket and daily quota on a real Redis (docker compose up -d redis)."""
from __future__ import annotations

import asyncio
import os

import pytest
from redis.asyncio import Redis

from config.loader import get_settings
from domain.value_objects.ids import TenantId
from jobqueue.rate_limiter import RedisRateLimiter
from services.ports.rate_limiter import Limits

pytestmark = pytest.mark.skipif(not os.environ.get("PULSE_E2E"), reason="set PULSE_E2E=1 (needs Redis)")


@pytest.fixture
async def limiter():
    r = get_settings().redis
    redis = Redis(host=r.host, port=r.port, db=13, password=r.password)
    await redis.flushdb()  # DB 13 belongs to this test
    try:
        yield RedisRateLimiter(redis), redis
    finally:
        await redis.flushdb()
        await redis.aclose()


async def test_burst_then_rejection_with_a_sensible_retry_after(limiter):
    lim, _ = limiter
    tenant, limits = TenantId.new(), Limits(rate_per_second=2, burst=3)
    results = [await lim.acquire(tenant, limits) for _ in range(4)]
    assert [d.allowed for d in results] == [True, True, True, False]
    denied = results[-1]
    assert denied.reason == "rate" and 0 < denied.retry_after_seconds <= 0.5   # one token at 2/s


async def test_tokens_refill_at_the_configured_rate(limiter):
    lim, _ = limiter
    tenant, limits = TenantId.new(), Limits(rate_per_second=50, burst=1)
    assert (await lim.acquire(tenant, limits)).allowed
    assert not (await lim.acquire(tenant, limits)).allowed
    await asyncio.sleep(0.05)                                   # 50/s => a token every 20ms
    assert (await lim.acquire(tenant, limits)).allowed


async def test_concurrent_requests_cannot_overspend_the_bucket(limiter):
    lim, _ = limiter
    tenant, limits = TenantId.new(), Limits(rate_per_second=0.001, burst=10)
    decisions = await asyncio.gather(*(lim.acquire(tenant, limits) for _ in range(60)))
    assert sum(d.allowed for d in decisions) == 10               # atomic: exactly the burst, no more


async def test_tenants_have_separate_buckets(limiter):
    lim, _ = limiter
    a, b, limits = TenantId.new(), TenantId.new(), Limits(rate_per_second=0.001, burst=1)
    assert (await lim.acquire(a, limits)).allowed and not (await lim.acquire(a, limits)).allowed
    assert (await lim.acquire(b, limits)).allowed


async def test_daily_quota_counts_denials_nothing_and_reports_remaining(limiter):
    lim, _ = limiter
    tenant, limits = TenantId.new(), Limits(rate_per_second=1000, burst=1000, daily_quota=3)
    results = [await lim.acquire(tenant, limits) for _ in range(5)]
    assert [d.allowed for d in results] == [True, True, True, False, False]
    assert [d.quota_remaining for d in results[:3]] == [2, 1, 0]
    assert results[3].reason == "quota" and 0 < results[3].retry_after_seconds <= 86400
    assert await lim.used_today(tenant) == 3                     # denied requests were not counted


async def test_refund_and_unlimited_quota(limiter):
    lim, redis = limiter
    tenant = TenantId.new()
    limited = Limits(rate_per_second=1000, burst=1000, daily_quota=1)
    assert (await lim.acquire(tenant, limited)).allowed
    assert not (await lim.acquire(tenant, limited)).allowed
    await lim.refund_quota(tenant)
    assert (await lim.acquire(tenant, limited)).allowed

    free = Limits(rate_per_second=1000, burst=1000, daily_quota=None)
    d = await lim.acquire(TenantId.new(), free)
    assert d.allowed and d.quota_remaining is None
    await lim.refund_quota(TenantId.new())                       # nothing to refund: harmless


async def test_a_rate_denial_does_not_consume_quota(limiter):
    lim, _ = limiter
    tenant, limits = TenantId.new(), Limits(rate_per_second=0.001, burst=1, daily_quota=5)
    assert (await lim.acquire(tenant, limits)).allowed
    for _ in range(3):
        assert (await lim.acquire(tenant, limits)).reason == "rate"
    assert await lim.used_today(tenant) == 1
