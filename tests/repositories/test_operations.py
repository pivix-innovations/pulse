"""Cross-tenant pipeline snapshot (feeds the operations gauges)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from domain.entities.dead_letter import DeadLetter
from domain.enums import ChannelType, DeliveryStatus
from domain.value_objects.ids import DeadLetterId
from tests.repositories.conftest import make_attempt, seed
from tests.repositories.test_delivery_attempts import add
from tests.repositories.test_queues import entry

NOW = datetime.now(timezone.utc)


async def test_snapshot_counts_the_pipeline_across_tenants(env):
    g1 = await seed(env)
    g2 = await seed(env)  # a different tenant: the snapshot is global
    lost = make_attempt(g1, next_retry_at=NOW - timedelta(minutes=30))
    in_doubt = make_attempt(g2)
    in_doubt.dispatch_started_at = NOW - timedelta(minutes=30)
    healthy = make_attempt(await seed(env, g1.tenant))
    healthy.dispatch_started_at = NOW - timedelta(seconds=5)
    sent = make_attempt(await seed(env, g1.tenant), ChannelType.PUSH)
    await add(env, lost, in_doubt, healthy, sent)
    sent.mark_sent(NOW, expects_confirmation=True)
    async with env.uow() as uow:
        for a in (lost, in_doubt, healthy, sent):  # persists dispatch_started_at / status
            await uow.delivery_attempts.update(a)
        await uow.outbox.add_many([entry(g1.tenant, 1), entry(g2.tenant, 2)])
        await uow.dead_letters.add(
            DeadLetter(
                id=DeadLetterId.new(), tenant_id=g1.tenant, attempt_id=lost.id,
                notification_id=g1.notification.id, channel=ChannelType.EMAIL, reason="boom",
            )
        )
        await uow.commit()

    async with env.uow() as uow:
        snap = await uow.operations.snapshot(
            NOW + timedelta(seconds=1), NOW - timedelta(minutes=5), NOW - timedelta(minutes=5)
        )

    assert snap.outbox_pending == 2
    assert snap.outbox_oldest_age_seconds >= 0
    assert snap.open_attempts == {
        (ChannelType.EMAIL, DeliveryStatus.PENDING): 3,
        (ChannelType.PUSH, DeliveryStatus.SENT): 1,
    }
    assert snap.stuck_attempts == 2
    assert snap.dead_letters == 1


async def test_snapshot_of_an_empty_system_is_all_zero(env):
    async with env.uow() as uow:
        snap = await uow.operations.snapshot(NOW, NOW, NOW)
    assert (snap.outbox_pending, snap.stuck_attempts, snap.dead_letters) == (0, 0, 0)
    assert snap.outbox_oldest_age_seconds == 0 and snap.open_attempts == {}
