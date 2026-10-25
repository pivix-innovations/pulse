from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from domain.entities.provider import Provider
from domain.entities.provider_call import ProviderCall
from domain.enums import ChannelType
from domain.value_objects.ids import DeliveryAttemptId, ProviderId, TenantId
from tests.repositories.conftest import seed

NOW = datetime.now(timezone.utc)


def call(g, provider, outcome="ok", latency=100, error=None, age=timedelta(0)):
    return ProviderCall(
        tenant_id=g.tenant, provider_id=provider.id, attempt_id=DeliveryAttemptId.new(),
        channel=ChannelType.EMAIL, outcome=outcome, latency_ms=latency, error=error,
        created_at=NOW - age,
    )


async def test_call_stats_aggregate_like_the_health_page_expects(env):
    g = await seed(env)
    p = g.provider
    async with env.uow() as uow:
        await uow.provider_calls.add_many([
            call(g, p, "ok", 100, age=timedelta(minutes=50)),
            call(g, p, "ok", 200, age=timedelta(minutes=40)),
            call(g, p, "ok", 300, age=timedelta(minutes=30)),
            call(g, p, "provider_error", 900, "503 upstream", age=timedelta(minutes=20)),
            call(g, p, "recipient_error", 50, "bad number", age=timedelta(minutes=10)),  # not the provider's fault
            call(g, p, "provider_error", 700, "ancient outage", age=timedelta(hours=30)),  # outside the window
        ])
        await uow.commit()
        stats = (await uow.provider_calls.stats(g.tenant, NOW - timedelta(hours=24)))[p.id]

    assert stats.calls == 4 and stats.failures == 1          # recipient errors excluded
    assert stats.avg_latency_ms == pytest.approx(200)         # successful calls only
    assert stats.p95_latency_ms == pytest.approx(290)         # continuous percentile of [100, 200, 300]
    assert stats.last_error == "503 upstream"
    assert stats.last_failure_at is not None and stats.last_success_at is not None
    assert stats.last_failure_at > stats.last_success_at     # the 503 came after the last success
    assert stats.recent == ["provider_error", "ok", "ok", "ok"]  # newest first, recipient errors skipped


async def test_call_stats_are_per_provider_and_per_tenant(env):
    g = await seed(env)
    second = Provider(id=ProviderId.new(), tenant_id=g.tenant, channel=ChannelType.EMAIL, name="console", label="second")
    stranger = await seed(env)
    async with env.uow() as uow:
        await uow.providers.add(second)
        await uow.provider_calls.add_many([
            call(g, g.provider), call(g, second, "provider_error", error="x"), call(stranger, stranger.provider),
        ])
        await uow.commit()
        mine = await uow.provider_calls.stats(g.tenant, NOW - timedelta(hours=1))
    assert set(mine) == {g.provider.id, second.id}
    assert mine[g.provider.id].failures == 0 and mine[second.id].failures == 1


async def test_prune_deletes_only_old_calls(env):
    g = await seed(env)
    async with env.uow() as uow:
        await uow.provider_calls.add_many([
            call(g, g.provider, age=timedelta(days=40)), call(g, g.provider, age=timedelta(days=1)),
        ])
        await uow.commit()
        assert await uow.provider_calls.prune(NOW - timedelta(days=30)) == 1
        await uow.commit()
        stats = await uow.provider_calls.stats(g.tenant, NOW - timedelta(days=60))
    assert stats[g.provider.id].calls == 1


async def test_providers_by_ids_are_tenant_scoped_and_unscoped_lookup_works(env):
    g = await seed(env)
    stranger = await seed(env)
    async with env.uow() as uow:
        got = await uow.providers.list_by_ids(g.tenant, [g.provider.id, stranger.provider.id, ProviderId.new()])
        assert [p.id for p in got] == [g.provider.id]           # another tenant's id is invisible
        assert await uow.providers.list_by_ids(g.tenant, []) == []
        assert (await uow.providers.get_unscoped(stranger.provider.id)).tenant_id == stranger.tenant
        assert await uow.providers.get_unscoped(ProviderId.new()) is None


async def test_provider_secrets_and_config_roundtrip(env):
    g = await seed(env)
    p = Provider(
        id=ProviderId.new(), tenant_id=g.tenant, channel=ChannelType.SMS, name="twilio", label="Alerts",
        config={"account_sid": "AC1", "nested": {"k": [1, 2]}}, secrets_encrypted="gAAAA-ciphertext",
        priority=3, is_active=False,
    )
    async with env.uow() as uow:
        await uow.providers.add(p)
        await uow.commit()
    async with env.uow() as uow:
        got = await uow.providers.get(g.tenant, p.id)
    assert (got.label, got.config, got.secrets_encrypted, got.priority, got.is_active) == (
        "Alerts", p.config, "gAAAA-ciphertext", 3, False,
    )


# ── constraints only Postgres enforces ─────────────────────────────────


async def test_two_providers_cannot_share_a_label_within_a_tenant(pg_env):
    g = await seed(pg_env)
    dup = Provider(id=ProviderId.new(), tenant_id=g.tenant, channel=ChannelType.SMS, name="console", label=g.provider.label)
    async with pg_env.uow() as uow:
        with pytest.raises(IntegrityError):
            await uow.providers.add(dup)
    # ...but another tenant may reuse the label.
    other = Provider(id=ProviderId.new(), tenant_id=TenantId.new(), channel=ChannelType.SMS, name="console", label=g.provider.label)
    async with pg_env.uow() as uow:
        await uow.providers.add(other)
        await uow.commit()


async def test_deleting_a_provider_removes_its_call_history(pg_env):
    g = await seed(pg_env)
    async with pg_env.uow() as uow:
        await uow.provider_calls.add_many([call(g, g.provider)])
        await uow.commit()
        assert await uow.providers.delete(g.tenant, g.provider.id)
        await uow.commit()
        assert await uow.provider_calls.stats(g.tenant, NOW - timedelta(hours=1)) == {}
