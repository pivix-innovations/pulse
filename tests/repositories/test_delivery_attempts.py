from __future__ import annotations

from datetime import datetime, timedelta, timezone

from domain.enums import ChannelType, DeliveryStatus
from domain.value_objects.ids import DeliveryAttemptId, ProviderId, TenantId
from tests.repositories.conftest import make_attempt, seed

NOW = datetime.now(timezone.utc)


async def add(env, *attempts):
    async with env.uow() as uow:
        await uow.delivery_attempts.add_many(list(attempts))
        await uow.commit()


async def reload(env, g, attempt):
    async with env.uow() as uow:
        return await uow.delivery_attempts.get(g.tenant, attempt.id)


async def test_roundtrip_and_tenant_isolation(env):
    g = await seed(env)
    a = make_attempt(g)
    await add(env, a)

    got = await reload(env, g, a)
    assert (got.channel, got.status, got.subject, got.body) == (ChannelType.EMAIL, DeliveryStatus.PENDING, "s", "b")
    assert got.attempt_count == 0 and got.expects_confirmation is True

    async with env.uow() as uow:
        assert await uow.delivery_attempts.get(TenantId.new(), a.id) is None  # someone else's tenant
        assert await uow.delivery_attempts.get(g.tenant, DeliveryAttemptId.new()) is None


async def test_update_persists_the_whole_lifecycle(env):
    g = await seed(env)
    a = make_attempt(g)
    await add(env, a)

    a.mark_dispatching(NOW)
    a.mark_sent(NOW, g.provider.id, expects_confirmation=False, provider_message_id="msg-1")
    async with env.uow() as uow:
        await uow.delivery_attempts.update(a)
        await uow.commit()

    got = await reload(env, g, a)
    assert got.status is DeliveryStatus.SENT and got.attempt_count == 1
    assert got.provider_id == g.provider.id and got.provider_message_id == "msg-1"
    assert got.expects_confirmation is False and got.dispatch_started_at is None
    assert got.sent_at is not None


async def test_find_by_dispatch_triple_and_by_provider_message_id(env):
    g = await seed(env)
    a = make_attempt(g)
    await add(env, a)
    a.mark_sent(NOW, g.provider.id, provider_message_id="receipt-key")
    async with env.uow() as uow:
        await uow.delivery_attempts.update(a)
        await uow.commit()

    async with env.uow() as uow:
        by_triple = await uow.delivery_attempts.get_by_dispatch(g.notification.id, g.recipient.id, ChannelType.EMAIL)
        assert by_triple.id == a.id
        assert await uow.delivery_attempts.get_by_dispatch(g.notification.id, g.recipient.id, ChannelType.SMS) is None

        by_receipt = await uow.delivery_attempts.get_by_provider_message_id(g.tenant, g.provider.id, "receipt-key")
        assert by_receipt.id == a.id
        # A receipt is only ever matched within its own tenant and provider.
        assert await uow.delivery_attempts.get_by_provider_message_id(TenantId.new(), g.provider.id, "receipt-key") is None
        assert await uow.delivery_attempts.get_by_provider_message_id(g.tenant, ProviderId.new(), "receipt-key") is None
        assert await uow.delivery_attempts.get_by_provider_message_id(g.tenant, g.provider.id, "other") is None


async def test_list_for_notification(env):
    g = await seed(env)
    await add(env, make_attempt(g, ChannelType.EMAIL), make_attempt(g, ChannelType.SMS))
    async with env.uow() as uow:
        got = await uow.delivery_attempts.list_for_notification(g.tenant, g.notification.id)
        assert {a.channel for a in got} == {ChannelType.EMAIL, ChannelType.SMS}
        assert await uow.delivery_attempts.list_for_notification(TenantId.new(), g.notification.id) == []


async def test_list_unconfirmed_for_recipient_is_sent_only_and_oldest_first(env):
    g = await seed(env)
    # One row per (notification, recipient, channel): spread over notifications.
    g2, g3 = await seed(env, g.tenant), await seed(env, g.tenant)
    older = make_attempt(g, ChannelType.IN_APP)
    newer = make_attempt(g2, ChannelType.IN_APP)
    newer.recipient_id = g.recipient.id
    delivered = make_attempt(g3, ChannelType.IN_APP)
    delivered.recipient_id = g.recipient.id
    pending = make_attempt(g, ChannelType.PUSH)
    await add(env, older, newer, delivered, pending)

    older.mark_sent(NOW - timedelta(minutes=5))
    newer.mark_sent(NOW - timedelta(minutes=1))
    delivered.mark_sent(NOW - timedelta(minutes=3))
    delivered.mark_delivered(NOW)
    async with env.uow() as uow:
        for a in (older, newer, delivered):
            await uow.delivery_attempts.update(a)
        await uow.commit()
        got = await uow.delivery_attempts.list_unconfirmed_for_recipient(g.tenant, g.recipient.id, ChannelType.IN_APP)
    assert [a.id for a in got] == [older.id, newer.id]


async def test_list_stale_only_returns_old_sent_attempts_that_expect_confirmation(env):
    g1 = await seed(env)
    g2, g3 = await seed(env, g1.tenant), await seed(env, g1.tenant)
    old_unconfirmed = make_attempt(g1, ChannelType.PUSH)
    old_final = make_attempt(g2, ChannelType.PUSH)     # provider never confirms: `sent` is terminal
    recent = make_attempt(g3, ChannelType.PUSH)
    await add(env, old_unconfirmed, old_final, recent)

    old_unconfirmed.mark_sent(NOW - timedelta(hours=2), expects_confirmation=True)
    old_final.mark_sent(NOW - timedelta(hours=2), expects_confirmation=False)
    recent.mark_sent(NOW, expects_confirmation=True)
    async with env.uow() as uow:
        for a in (old_unconfirmed, old_final, recent):
            await uow.delivery_attempts.update(a)
        await uow.commit()
        got = await uow.delivery_attempts.list_stale(
            DeliveryStatus.SENT, ChannelType.PUSH, NOW - timedelta(hours=1), limit=10
        )
    assert [a.id for a in got] == [old_unconfirmed.id]


async def test_list_stuck_separates_lost_jobs_from_workers_that_died(env):
    g = await seed(env)
    gs = [g] + [await seed(env, g.tenant) for _ in range(4)]
    lost_before, in_doubt_before = NOW - timedelta(minutes=5), NOW - timedelta(minutes=5)

    lost = make_attempt(gs[0], next_retry_at=NOW - timedelta(minutes=30))      # never claimed, long overdue
    in_doubt = make_attempt(gs[1])                                              # claimed long ago, no outcome
    in_doubt.dispatch_started_at = NOW - timedelta(minutes=30)
    working = make_attempt(gs[2])                                               # claimed a moment ago
    working.dispatch_started_at = NOW - timedelta(seconds=10)
    not_due = make_attempt(gs[3], next_retry_at=NOW + timedelta(minutes=10))    # scheduled retry in the future
    done = make_attempt(gs[4])                                                  # terminal
    await add(env, lost, in_doubt, working, not_due, done)
    done.mark_sent(NOW - timedelta(hours=1), expects_confirmation=False)
    async with env.uow() as uow:
        for a in (lost, in_doubt, working, not_due, done):
            await uow.delivery_attempts.update(a)
        await uow.commit()
        got = await uow.delivery_attempts.list_stuck(lost_before, in_doubt_before, limit=10)
    assert {a.id for a in got} == {lost.id, in_doubt.id}
