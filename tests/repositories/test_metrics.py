"""Metrics aggregates: the time buckets must group the same way in the fakes and in Postgres (date_bin)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from domain.enums import ChannelType, DeliveryStatus
from tests.repositories.conftest import make_attempt, seed


async def test_attempts_timeline_groups_into_fixed_buckets_by_status_and_tenant(env):
    g = await seed(env)
    stranger = await seed(env)
    sent = make_attempt(g, ChannelType.EMAIL)
    sent.mark_sent(datetime.now(timezone.utc))
    async with env.uow() as uow:
        await uow.delivery_attempts.add_many([sent, make_attempt(g, ChannelType.IN_APP), make_attempt(stranger)])
        await uow.commit()

    since = datetime.now(timezone.utc) - timedelta(hours=1)
    async with env.uow() as uow:
        points = await uow.metrics.attempts_timeline(g.tenant, since, 86400)   # one day-wide bucket: everything lands in it
        assert sum(n for _, _, n in points) == 2                               # not the other tenant's
        assert {s for _, s, _ in points} == {DeliveryStatus.SENT, DeliveryStatus.PENDING}
        assert len({b for b, _, _ in points}) == 1
        bucket = points[0][0]
        assert bucket.timestamp() % 86400 == 0                                 # aligned to the fixed origin

        assert await uow.metrics.attempts_timeline(g.tenant, datetime.now(timezone.utc) + timedelta(hours=1), 60) == []
