from __future__ import annotations

from datetime import datetime, timedelta, timezone

from domain.entities.provider_call import ProviderCall
from domain.enums import ChannelType
from domain.value_objects.ids import DeliveryAttemptId, ProviderId, TenantId
from services.ports.providers import ProviderSendError
from services.ports.repositories import ProviderCallStats
from services.provider_health import assess
from tests.fakes import FakeProvider, InMemoryUnitOfWork
from tests.test_delivery_failover import deliver


def stats(calls, failures, recent=()):
    return ProviderCallStats(calls=calls, failures=failures, recent=list(recent))


def test_no_calls_means_idle_not_healthy():
    assert assess(None).status == "idle"
    assert assess(stats(0, 0)).status == "idle"


def test_a_clean_record_is_healthy_with_a_success_rate():
    h = assess(stats(100, 1, ["ok"] * 5))
    assert h.status == "healthy" and h.success_rate == 0.99


def test_degraded_below_95_percent_with_enough_calls():
    assert assess(stats(100, 10, ["ok", "provider_error", "ok"])).status == "degraded"


def test_down_below_half_or_after_three_straight_failures():
    assert assess(stats(10, 6, ["ok", "ok"])).status == "down"
    # Great history, but failing right now.
    assert assess(stats(200, 3, ["provider_error"] * 3)).status == "down"


def test_one_early_failure_is_degraded_not_down():
    assert assess(stats(2, 1, ["provider_error", "ok"])).status == "degraded"


# ── recorded from real deliveries ──────────────────────────────────────


async def test_each_call_in_a_failover_chain_is_recorded_against_its_own_provider():
    first, second = FakeProvider(fail=True), FakeProvider()
    result, attempt, uow, tenant = await deliver([first, second])
    assert result == "sent"
    calls = uow.provider_calls.items
    assert [c.outcome for c in calls] == ["provider_error", "ok"]
    assert calls[0].provider_id != calls[1].provider_id
    assert "provider exploded" in calls[0].error
    assert all(c.attempt_id == attempt.id and c.latency_ms >= 0 for c in calls)


async def test_a_bad_recipient_is_not_held_against_the_provider():
    bad = FakeProvider(error=ProviderSendError("bad number", retryable=False, failover=False))
    _, _, uow, tenant = await deliver([bad, FakeProvider()])
    [call] = uow.provider_calls.items
    assert call.outcome == "recipient_error"
    s = (await uow.provider_calls.stats(tenant, datetime.now(timezone.utc) - timedelta(hours=1)))[call.provider_id]
    assert s.calls == 0 and s.failures == 0
    assert assess(s).status == "idle"


async def test_the_unsaved_env_default_has_no_health_record():
    # One provider => FakeProviderRegistry gives provider_id None (the env default).
    _, _, uow, _ = await deliver(FakeProvider())
    assert uow.provider_calls.items == []


async def test_stats_window_and_last_error():
    uow = InMemoryUnitOfWork()
    tenant, pid = TenantId.new(), ProviderId.new()
    now = datetime.now(timezone.utc)

    def call(outcome, age_h, error=None):
        return ProviderCall(
            tenant_id=tenant, provider_id=pid, attempt_id=DeliveryAttemptId.new(),
            channel=ChannelType.EMAIL, outcome=outcome, latency_ms=100, error=error,
            created_at=now - timedelta(hours=age_h),
        )

    await uow.provider_calls.add_many([
        call("provider_error", 30, "ancient outage"),
        call("ok", 3), call("provider_error", 2, "timed out"), call("ok", 1),
    ])
    s = (await uow.provider_calls.stats(tenant, now - timedelta(hours=24)))[pid]
    assert (s.calls, s.failures, s.last_error) == (3, 1, "timed out")  # the 30h-old call is out of window
