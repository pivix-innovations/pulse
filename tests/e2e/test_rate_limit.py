"""C1 on the real stack: the API's Redis limiter, tenant overrides and quota."""
from __future__ import annotations

import uuid

import pytest

from tests.e2e.harness import Stack, wait_for


@pytest.fixture(scope="module")
def stack():
    s = Stack("limits", redis_db=13, env={"PULSE_RATE_LIMIT__LIMITS_CACHE_SECONDS": "0.2"}).start()
    try:
        yield s
    except BaseException:
        print(s.dump_logs())
        raise
    finally:
        s.stop()


def send(stack, event_type="user.signup"):
    return stack.api("POST", "/events", json={
        "event_type": event_type, "source": "e2e", "idempotency_key": str(uuid.uuid4()),
        "payload": {"recipients": [{"external_ref": "ada", "email": "ada@example.com"}]},
    })


def set_limits(stack, rate, burst, quota):
    stack.sql(
        "UPDATE tenants SET rate_limit_per_second = $1, rate_burst = $2, daily_quota = $3 WHERE id = $4::uuid",
        rate, burst, quota, stack.tenant_id,
    )


def test_a_tenant_over_its_burst_gets_429_with_retry_after_then_recovers(stack):
    set_limits(stack, 0.2, 3, None)   # a token every 5s: the burst is what matters
    wait_for(lambda: stack.api("GET", "/tenant/usage").json()["rate_burst"] == 3, what="new limits to be picked up")

    codes = [send(stack).status_code for _ in range(5)]
    assert codes[:3] == [202, 202, 202] and codes[3:] == [429, 429]
    blocked = send(stack)
    assert blocked.status_code == 429
    assert int(blocked.headers["Retry-After"]) >= 1 and blocked.headers["X-RateLimit-Reason"] == "rate"

    wait_for(lambda: send(stack).status_code == 202, timeout=15, interval=1, what="the bucket to refill")


def test_daily_quota_stops_ingest_and_the_usage_endpoint_agrees(stack):
    set_limits(stack, 1000.0, 1000, 4)
    wait_for(lambda: stack.api("GET", "/tenant/usage").json()["daily_quota"] == 4, what="new limits")
    before = stack.api("GET", "/tenant/usage").json()["used_today"]

    accepted = 0
    for _ in range(10):
        r = send(stack)
        if r.status_code == 202:
            accepted += 1
        else:
            assert r.status_code == 429 and r.headers["X-RateLimit-Reason"] == "quota"
            break
    usage = stack.api("GET", "/tenant/usage").json()
    assert usage["used_today"] == 4 and usage["quota_remaining"] == 0
    assert accepted == 4 - before                                       # quota was shared with the previous test's count


def test_limits_are_per_tenant_in_redis(stack):
    keys = stack.redis_keys("pulse:rl:*")
    assert any(k.startswith("pulse:rl:bucket:") and stack.tenant_id in k for k in keys)
    assert any(k.startswith("pulse:rl:quota:") and stack.tenant_id in k for k in keys)
