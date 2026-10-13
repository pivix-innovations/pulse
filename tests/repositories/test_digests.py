"""Digest repository contract: joining open batches, due-claiming, flush state, open checks."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from domain.entities.digest import DigestStatus
from domain.value_objects.ids import NotificationId
from tests.repositories.conftest import seed

NOW = datetime.now(timezone.utc)


async def add(uow, g, notification_id=None, key="", payload=None, now=NOW, window=600, max_items=100):
    return await uow.digests.add_item(
        tenant_id=g.tenant, workflow_id=g.notification.workflow_id, recipient_id=g.recipient.id,
        digest_key=key, notification_id=notification_id or g.notification.id,
        payload=payload or {"n": 1}, now=now, window_seconds=window, max_items=max_items,
    )


async def notification(env, g):
    """Another notification of the same workflow (each event has its own)."""
    from domain.entities.notification import Notification
    from domain.value_objects.ids import NotificationId as N

    async with env.uow() as uow:
        n = Notification(id=N.new(), tenant_id=g.tenant, event_id=g.notification.event_id,
                         workflow_id=g.notification.workflow_id)
        await uow.notifications.add(n)
        await uow.commit()
    return n


async def test_events_join_the_open_batch_and_the_first_one_anchors_it(env):
    g = await seed(env)
    n2, n3 = await notification(env, g), await notification(env, g)
    batches = []
    for nid, n in ((None, 1), (n2.id, 2), (n3.id, 3)):      # one transaction per event, as at ingest
        async with env.uow() as uow:
            batches.append(await add(uow, g, nid, payload={"n": n}))
            await uow.commit()
    first, second, third = batches
    assert first.id == second.id == third.id and third.item_count == 3
    assert third.anchor_notification_id == g.notification.id
    assert abs((third.flush_at - (NOW + timedelta(minutes=10))).total_seconds()) < 1
    async with env.uow() as uow:
        items = await uow.digests.list_items(first.id)
    assert [i.payload["n"] for i in items] == [1, 2, 3]


async def test_batches_are_separate_per_key(env):
    g = await seed(env)
    n2 = await notification(env, g)
    async with env.uow() as uow:
        a = await add(uow, g, key="A")
        b = await add(uow, g, n2.id, key="B")
        await uow.commit()
    assert a.id != b.id


async def test_a_full_batch_is_due_immediately_and_claiming_skips_future_ones(env):
    g = await seed(env)
    n2, n3 = await notification(env, g), await notification(env, g)
    async with env.uow() as uow:
        await add(uow, g, key="slow")                              # window still open
        await add(uow, g, n2.id, key="full", max_items=2)
        await add(uow, g, n3.id, key="full", max_items=2)          # reaches the cap
        await uow.commit()
    async with env.uow() as uow:
        due = await uow.digests.claim_due(NOW + timedelta(seconds=1), limit=10)
    assert [b.digest_key for b in due] == ["full"]


async def test_a_flushed_batch_stops_collecting_and_a_new_one_opens(env):
    g = await seed(env)
    n2 = await notification(env, g)
    async with env.uow() as uow:
        first = await add(uow, g)
        assert await uow.digests.has_open_for_notification(g.notification.id)
        await uow.digests.mark_flushed(first, NOW)
        assert not await uow.digests.has_open_for_notification(g.notification.id)
        again = await add(uow, g, n2.id)
        await uow.commit()
    assert again.id != first.id and again.anchor_notification_id == n2.id and again.status is DigestStatus.OPEN
    async with env.uow() as uow:
        assert await uow.digests.claim_due(NOW + timedelta(days=1), 10) == [
            b for b in await uow.digests.claim_due(NOW + timedelta(days=1), 10) if b.id == again.id
        ]


async def test_get_for_notification_is_tenant_scoped(env):
    g = await seed(env)
    stranger = await seed(env)
    async with env.uow() as uow:
        batch = await add(uow, g)
        await uow.commit()
        assert (await uow.digests.get_for_notification(g.tenant, g.notification.id)).id == batch.id
        assert await uow.digests.get_for_notification(stranger.tenant, g.notification.id) is None
        assert await uow.digests.get_for_notification(g.tenant, NotificationId.new()) is None
