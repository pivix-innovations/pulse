from __future__ import annotations

import pytest

from config.ratelimit import RateLimitSettings
from domain.value_objects.ids import TenantId
from services.rate_limit_service import RateLimitService
from tests.api.conftest import PASSWORD
from tests.fakes import FakeRateLimiter

EVENT = {"event_type": "user.signup", "source": "quotient", "payload": {"plan": "pro"}}
SETTINGS = RateLimitSettings(default_rate_per_second=1, default_burst=3, default_daily_quota=None,
                             limits_cache_seconds=0.01)


@pytest.fixture
def limited(world):
    limiter = FakeRateLimiter()
    world.app.state.rate_limit_service = RateLimitService(limiter, lambda: world.uow, SETTINGS)
    world.limiter = limiter
    return world


async def post(world, key="k", headers=None):
    return await world.client.post(
        "/api/v1/events", json={**EVENT, "idempotency_key": key}, headers=headers or world.key_headers
    )


async def test_burst_is_accepted_then_429_with_retry_after(limited):
    codes = [(await post(limited, f"k{i}")).status_code for i in range(4)]
    assert codes == [202, 202, 202, 429]
    r = await post(limited, "k-more")
    assert r.status_code == 429
    assert int(r.headers["Retry-After"]) >= 1
    assert r.headers["X-RateLimit-Reason"] == "rate"
    assert "request rate limit" in r.json()["detail"]


async def test_accepted_responses_say_how_much_headroom_is_left(limited):
    r = await post(limited, "a")
    assert r.status_code == 202
    assert r.headers["X-RateLimit-Limit"] == "1/s burst 3"
    assert r.headers["X-RateLimit-Remaining"] == "2"


async def test_tokens_refill_over_time(limited):
    for i in range(3):
        await post(limited, f"k{i}")
    assert (await post(limited, "blocked")).status_code == 429
    limited.limiter.now += 2
    assert (await post(limited, "after")).status_code == 202


async def test_daily_quota_blocks_with_a_different_reason(limited):
    tenant = await limited.uow.tenants.get(limited.tenant_id)
    tenant.daily_quota, tenant.rate_burst = 2, 100
    limited.app.state.rate_limit_service.forget(limited.tenant_id)
    assert [(await post(limited, f"q{i}")).status_code for i in range(3)] == [202, 202, 429]
    r = await post(limited, "q-more")
    assert r.headers["X-RateLimit-Reason"] == "quota" and "daily event quota" in r.json()["detail"]
    ok = await limited.client.get("/api/v1/events", headers=limited.key_headers)
    assert ok.status_code == 200          # only POST /events is limited


async def test_a_rejected_event_does_not_spend_quota(limited):
    tenant = await limited.uow.tenants.get(limited.tenant_id)
    tenant.daily_quota, tenant.rate_burst = 1, 100
    limited.app.state.rate_limit_service.forget(limited.tenant_id)
    bad = await limited.client.post(
        "/api/v1/events", json={"event_type": "no.such.event", "source": "x", "idempotency_key": "bad"},
        headers=limited.key_headers,
    )
    assert bad.status_code == 422
    assert (await post(limited, "good")).status_code == 202        # quota unit was refunded


async def test_another_tenant_is_unaffected_by_a_noisy_one(limited):
    from services.identity_service import IdentityService
    from domain.entities.identity import Tenant

    other = Tenant(id=TenantId.new(), name="Quiet", slug="quiet")
    await limited.uow.tenants.add(other)
    other_key, _ = await IdentityService(lambda: limited.uow).create_api_key(other.id, "k")
    for i in range(5):
        await post(limited, f"noisy{i}")
    assert (await post(limited, "noisy-again")).status_code == 429
    assert (await post(limited, "q1", headers={"X-API-Key": other_key})).status_code == 202


async def test_no_limiter_configured_means_no_limits(world):
    codes = [(await post(world, f"k{i}")).status_code for i in range(10)]
    assert set(codes) == {202}


async def test_a_limiter_outage_never_blocks_ingest(limited):
    limited.limiter.fail = True
    assert (await post(limited, "k")).status_code == 202


# ── visibility and admin control ───────────────────────────────────────


async def test_usage_endpoint_reports_limits_and_todays_use(limited):
    tenant = await limited.uow.tenants.get(limited.tenant_id)
    tenant.daily_quota, tenant.rate_burst = 10, 100
    limited.app.state.rate_limit_service.forget(limited.tenant_id)
    await post(limited, "u1")
    await post(limited, "u2")
    r = await limited.client.get("/api/v1/tenant/usage", headers=limited.key_headers)
    assert r.status_code == 200
    assert r.json() == {
        "rate_limit_per_second": 1.0, "rate_burst": 100, "daily_quota": 10,
        "used_today": 2, "quota_remaining": 8, "enforced": True,
    }


async def test_usage_says_so_when_limits_are_not_enforced(world):
    r = await world.client.get("/api/v1/tenant/usage", headers=world.key_headers)
    assert r.json()["enforced"] is False


async def admin_session(world):
    user = await world.uow.users.get_by_email("ada@example.com")
    user.is_platform_admin = True
    await world.uow.users.update(user)
    r = await world.client.post("/api/v1/auth/login", json={"email": "ada@example.com", "password": PASSWORD})
    assert r.status_code == 200


async def test_platform_admin_sets_and_clears_a_tenants_limits(limited):
    await admin_session(limited)
    url = f"/api/v1/admin/tenants/{limited.tenant_id}"

    r = await limited.client.patch(url, json={"rate_burst": 2, "daily_quota": 500})
    assert r.status_code == 200, r.text
    assert (r.json()["rate_burst"], r.json()["daily_quota"], r.json()["rate_limit_per_second"]) == (2, 500, None)
    assert [(await post(limited, f"a{i}")).status_code for i in range(3)] == [202, 202, 429]   # takes effect now

    r = await limited.client.patch(url, json={"name": "Renamed"})                # unrelated edit keeps limits
    assert r.json()["rate_burst"] == 2 and r.json()["name"] == "Renamed"

    r = await limited.client.patch(url, json={"rate_burst": None, "daily_quota": None})  # back to defaults
    assert r.json()["rate_burst"] is None and r.json()["daily_quota"] is None


async def test_limit_values_are_validated(limited):
    await admin_session(limited)
    url = f"/api/v1/admin/tenants/{limited.tenant_id}"
    for bad in ({"rate_limit_per_second": 0}, {"rate_burst": 0}, {"daily_quota": -5}):
        assert (await limited.client.patch(url, json=bad)).status_code == 422


async def test_tenant_members_cannot_change_their_own_limits(limited):
    r = await limited.client.patch(
        f"/api/v1/admin/tenants/{limited.tenant_id}", json={"rate_burst": 1_000_000}, headers=limited.key_headers
    )
    assert r.status_code in (401, 403)
