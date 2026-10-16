"""Outbox and notification queues: ordering, state filters and SKIP LOCKED."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


from domain.entities.notification import Notification
from domain.entities.outbox import OutboxEntry
from domain.enums import ChannelType, DeliveryStatus, NotificationStatus, OutboxStatus
from domain.value_objects.ids import NotificationId, OutboxEntryId, TenantId
from tests.repositories.conftest import make_attempt, seed

NOW = datetime.now(timezone.utc)


def entry(tenant, n=0):
    return OutboxEntry(
        id=OutboxEntryId.new(), tenant_id=tenant, aggregate_type="delivery_attempt",
        aggregate_id=f"agg-{n}", topic="deliver.email", payload={"n": n},
    )


# ── outbox ─────────────────────────────────────────────────────────────


async def test_outbox_claims_pending_oldest_first_and_publishing_removes_them(env):
    tenant = TenantId.new()
    entries = [entry(tenant, i) for i in range(3)]
    async with env.uow() as uow:
        for e in entries:  # separate flushes so created_at is strictly ordered
            await uow.outbox.add_many([e])
            await uow.commit()

    async with env.uow() as uow:
        claimed = await uow.outbox.claim_pending(limit=2)
        assert [c.payload["n"] for c in claimed] == [0, 1]
        await uow.outbox.mark_published([c.id for c in claimed])
        await uow.commit()

    async with env.uow() as uow:
        rest = await uow.outbox.claim_pending(limit=10)
    assert [c.payload["n"] for c in rest] == [2]


async def test_outbox_payload_roundtrips(env):
    tenant = TenantId.new()
    e = entry(tenant)
    e.payload = {"attempt_id": "a", "defer_until": "2026-10-13T10:00:00+00:00", "nested": {"x": [1, 2]}}
    async with env.uow() as uow:
        await uow.outbox.add_many([e])
        await uow.commit()
    async with env.uow() as uow:
        [got] = await uow.outbox.claim_pending(10)
    assert got.payload == e.payload and got.status is OutboxStatus.PENDING


# ── notifications ──────────────────────────────────────────────────────


async def add_notification(env, g, **fields):
    async with env.uow() as uow:
        n = Notification(
            id=NotificationId.new(), tenant_id=g.tenant,
            event_id=g.notification.event_id, workflow_id=g.notification.workflow_id, **fields,
        )
        await uow.notifications.add(n)
        await uow.commit()
    return n


async def test_list_due_returns_only_created_notifications_whose_time_has_come(env):
    g = await seed(env)
    due = await add_notification(env, g, scheduled_at=NOW - timedelta(minutes=1))
    await add_notification(env, g, scheduled_at=NOW + timedelta(hours=1))                    # future
    await add_notification(env, g)                                                           # not scheduled
    processing = await add_notification(env, g, scheduled_at=NOW - timedelta(minutes=1))
    processing.mark_processing()
    async with env.uow() as uow:
        await uow.notifications.update(processing)
        await uow.commit()
        got = await uow.notifications.list_due(NOW, limit=10)
    assert [n.id for n in got] == [due.id]


async def test_list_settleable_needs_processing_and_no_open_attempts(env):
    g = await seed(env)
    ready = await add_notification(env, g, status=NotificationStatus.PROCESSING)
    blocked = await add_notification(env, g, status=NotificationStatus.PROCESSING)
    awaiting_receipt = await add_notification(env, g, status=NotificationStatus.PROCESSING)
    finished = await add_notification(env, g, status=NotificationStatus.PROCESSING)
    already = await add_notification(env, g, status=NotificationStatus.COMPLETED)

    def attempt_for(n, **kw):
        a = make_attempt(g, **kw)
        a.notification_id = n.id
        return a

    delivered = attempt_for(ready)
    delivered.mark_sent(NOW)
    delivered.mark_delivered(NOW)
    pending = attempt_for(blocked)                                     # still to send
    unconfirmed = attempt_for(awaiting_receipt)
    unconfirmed.mark_sent(NOW, expects_confirmation=True)              # sent, waiting for a receipt
    fire_and_forget = attempt_for(finished)
    fire_and_forget.mark_sent(NOW, expects_confirmation=False)         # sent and final
    async with env.uow() as uow:
        await uow.delivery_attempts.add_many([delivered, pending, unconfirmed, fire_and_forget])
        await uow.commit()
        got = await uow.notifications.list_settleable(limit=10)
    assert {n.id for n in got} == {ready.id, finished.id}
    assert already.id not in {n.id for n in got}


async def test_list_for_tenant_pages_newest_first_filters_and_counts(env):
    g = await seed(env)
    made = []
    for _ in range(4):
        made.append(await add_notification(env, g))
    done = await add_notification(env, g, status=NotificationStatus.COMPLETED)
    other = await seed(env)  # another tenant's notification never shows up

    async with env.uow() as uow:
        page, total = await uow.notifications.list_for_tenant(g.tenant, limit=2, offset=0)
        assert total == 6 and len(page) == 2  # 4 + 1 + the seed notification
        completed, completed_total = await uow.notifications.list_for_tenant(
            g.tenant, status=NotificationStatus.COMPLETED
        )
        assert completed_total == 1 and [n.id for n in completed] == [done.id]
        mine, mine_total = await uow.notifications.list_for_tenant(g.tenant, limit=100)
        assert other.notification.id not in {n.id for n in mine}
        assert mine_total == len(mine)


async def test_list_for_tenant_searches_by_event_type_recipient_and_channel(env):
    g = await seed(env)
    quiet = await add_notification(env, g)                    # same event, no attempts at all
    async with env.uow() as uow:
        await uow.delivery_attempts.add_many([make_attempt(g, ChannelType.EMAIL), make_attempt(g, ChannelType.IN_APP)])
        await uow.commit()
    other = await seed(env)                                   # another tenant, same event type "t.e"
    async with env.uow() as uow:
        await uow.delivery_attempts.add_many([make_attempt(other)])
        await uow.commit()

    async with env.uow() as uow:
        by_event, n_event = await uow.notifications.list_for_tenant(g.tenant, event_type="t.e")
        assert n_event == 2 and other.notification.id not in {n.id for n in by_event}
        assert (await uow.notifications.list_for_tenant(g.tenant, event_type="no.such"))[1] == 0

        by_person, n_person = await uow.notifications.list_for_tenant(g.tenant, recipient_id=g.recipient.id)
        assert [n.id for n in by_person] == [g.notification.id] and n_person == 1   # one row despite two attempts

        by_channel, _ = await uow.notifications.list_for_tenant(g.tenant, channel=ChannelType.IN_APP)
        assert [n.id for n in by_channel] == [g.notification.id]
        assert (await uow.notifications.list_for_tenant(g.tenant, channel=ChannelType.SMS))[1] == 0

        both, _ = await uow.notifications.list_for_tenant(
            g.tenant, event_type="t.e", recipient_id=g.recipient.id, channel=ChannelType.EMAIL
        )
        assert [n.id for n in both] == [g.notification.id]
        assert quiet.id not in {n.id for n in by_person}


# ── SKIP LOCKED (real Postgres only) ───────────────────────────────────


async def test_outbox_relays_never_claim_the_same_entry(pg_env):
    tenant = TenantId.new()
    async with pg_env.uow() as uow:
        for i in range(10):
            await uow.outbox.add_many([entry(tenant, i)])
            await uow.commit()

    async with pg_env.uow() as relay_a:
        first = await relay_a.outbox.claim_pending(limit=5)
        async with pg_env.uow() as relay_b:      # a second relay polls while A still holds its locks
            second = await relay_b.outbox.claim_pending(limit=5)
        assert len(first) == len(second) == 5
        assert not {e.id for e in first} & {e.id for e in second}
        assert {e.payload["n"] for e in first} == {0, 1, 2, 3, 4}
        assert {e.payload["n"] for e in second} == {5, 6, 7, 8, 9}

    async with pg_env.uow() as relay_c:          # A rolled back: nothing was lost
        assert len(await relay_c.outbox.claim_pending(limit=20)) == 10


async def test_stuck_attempts_are_claimed_by_one_sweeper_at_a_time(pg_env):
    g = await seed(pg_env)
    gs = [g] + [await seed(pg_env, g.tenant) for _ in range(2)]
    attempts = []
    for gx in gs:
        a = make_attempt(gx)
        a.dispatch_started_at = NOW - timedelta(hours=1)
        attempts.append(a)
    async with pg_env.uow() as uow:
        await uow.delivery_attempts.add_many(attempts)
        await uow.commit()
        for a in attempts:  # add_many doesn't persist the claim; update does
            await uow.delivery_attempts.update(a)
        await uow.commit()

    cutoff = NOW - timedelta(minutes=5)
    async with pg_env.uow() as sweeper_a:
        mine = await sweeper_a.delivery_attempts.list_stuck(cutoff, cutoff, limit=10)
        async with pg_env.uow() as sweeper_b:
            theirs = await sweeper_b.delivery_attempts.list_stuck(cutoff, cutoff, limit=10)
        assert len(mine) == 3 and theirs == []   # B skips what A has locked instead of waiting or duplicating
    async with pg_env.uow() as sweeper_c:
        assert len(await sweeper_c.delivery_attempts.list_stuck(cutoff, cutoff, limit=10)) == 3


async def test_due_and_settleable_notifications_are_skip_locked(pg_env):
    g = await seed(pg_env)
    due = await add_notification(pg_env, g, scheduled_at=NOW - timedelta(minutes=1))
    done = await add_notification(pg_env, g, status=NotificationStatus.PROCESSING)

    async with pg_env.uow() as a:
        assert [n.id for n in await a.notifications.list_due(NOW, 10)] == [due.id]
        assert [n.id for n in await a.notifications.list_settleable(10)] == [done.id]
        async with pg_env.uow() as b:
            assert await b.notifications.list_due(NOW, 10) == []
            assert await b.notifications.list_settleable(10) == []


async def test_stale_sent_attempts_are_skip_locked(pg_env):
    g = await seed(pg_env)
    a = make_attempt(g)
    async with pg_env.uow() as uow:
        await uow.delivery_attempts.add_many([a])
        a.mark_sent(NOW - timedelta(hours=3), expects_confirmation=True)
        await uow.delivery_attempts.update(a)
        await uow.commit()

    older = NOW - timedelta(hours=1)
    async with pg_env.uow() as first:
        got = await first.delivery_attempts.list_stale(DeliveryStatus.SENT, a.channel, older, 10)
        assert len(got) == 1
        async with pg_env.uow() as second:
            assert await second.delivery_attempts.list_stale(DeliveryStatus.SENT, a.channel, older, 10) == []
