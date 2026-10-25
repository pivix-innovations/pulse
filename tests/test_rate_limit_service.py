from __future__ import annotations

from config.ratelimit import RateLimitSettings
from domain.entities.identity import Tenant
from domain.value_objects.ids import TenantId
from services.ports.rate_limiter import Limits
from services.rate_limit_service import RateLimitService
from tests.fakes import FakeRateLimiter, InMemoryUnitOfWork

DEFAULTS = RateLimitSettings(default_rate_per_second=10, default_burst=20, default_daily_quota=1000)


async def make(**tenant_fields):
    uow = InMemoryUnitOfWork()
    tenant = Tenant(id=TenantId.new(), name="T", slug="t", **tenant_fields)
    await uow.tenants.add(tenant)
    limiter = FakeRateLimiter()
    return RateLimitService(limiter, lambda: uow, DEFAULTS), tenant, uow, limiter


async def test_tenant_overrides_win_and_unset_fields_use_the_defaults():
    service, tenant, *_ = await make(rate_limit_per_second=2.5, daily_quota=50)
    assert await service.limits_for(tenant.id) == Limits(rate_per_second=2.5, burst=20, daily_quota=50)


async def test_a_tenant_with_no_overrides_gets_the_platform_defaults():
    service, tenant, *_ = await make()
    assert await service.limits_for(tenant.id) == Limits(10, 20, 1000)


async def test_limits_are_cached_until_forgotten():
    service, tenant, uow, _ = await make(rate_burst=5)
    assert (await service.limits_for(tenant.id)).burst == 5
    tenant.rate_burst = 8
    assert (await service.limits_for(tenant.id)).burst == 5      # cached
    service.forget(tenant.id)
    assert (await service.limits_for(tenant.id)).burst == 8


async def test_burst_then_rejection_then_refill():
    service, tenant, _, limiter = await make(rate_limit_per_second=1, rate_burst=3, daily_quota=None)
    results = [(await service.check(tenant.id))[0].allowed for _ in range(4)]
    assert results == [True, True, True, False]
    decision, _ = await service.check(tenant.id)
    assert decision.reason == "rate" and 0 < decision.retry_after_seconds <= 1
    limiter.now += 1.0                                            # one token refilled
    assert (await service.check(tenant.id))[0].allowed


async def test_daily_quota_caps_accepted_events_and_reports_what_is_left():
    service, tenant, *_ = await make(rate_limit_per_second=1000, rate_burst=1000, daily_quota=2)
    first, _ = await service.check(tenant.id)
    second, _ = await service.check(tenant.id)
    third, _ = await service.check(tenant.id)
    assert (first.quota_remaining, second.quota_remaining) == (1, 0)
    assert not third.allowed and third.reason == "quota"


async def test_refund_gives_the_quota_back():
    service, tenant, *_ = await make(rate_limit_per_second=1000, rate_burst=1000, daily_quota=1)
    assert (await service.check(tenant.id))[0].allowed
    await service.refund(tenant.id)
    assert (await service.check(tenant.id))[0].allowed


async def test_tenants_have_independent_buckets():
    service, a, uow, _ = await make(rate_burst=1, rate_limit_per_second=0.001, daily_quota=None)
    b = Tenant(id=TenantId.new(), name="B", slug="b", rate_burst=1, rate_limit_per_second=0.001)
    await uow.tenants.add(b)
    assert (await service.check(a.id))[0].allowed and not (await service.check(a.id))[0].allowed
    assert (await service.check(b.id))[0].allowed       # A's flood doesn't touch B


async def test_a_limiter_outage_lets_traffic_through():
    service, tenant, _, limiter = await make()
    limiter.fail = True
    decision, limits = await service.check(tenant.id)
    assert decision.allowed and limits is None


async def test_disabled_means_no_limiting_at_all():
    service, tenant, uow, limiter = await make()
    service._settings = RateLimitSettings(enabled=False)
    for _ in range(500):
        assert (await service.check(tenant.id))[0].allowed
    assert limiter.buckets == {}
