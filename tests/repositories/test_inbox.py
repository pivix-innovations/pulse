"""Inbox repository contract: idempotent add, paging order, read/archive state, scoping, pruning."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from domain.entities.inbox import InboxMessage
from domain.value_objects.ids import InboxMessageId
from tests.repositories.conftest import make_attempt, seed
from tests.repositories.test_delivery_attempts import add

NOW = datetime.now(timezone.utc)


async def test_add_is_idempotent_per_attempt(env):
    g = await seed(env)
    attempt = make_attempt(g)
    await add(env, attempt)

    def msg(body):
        return InboxMessage(
            id=InboxMessageId.new(), tenant_id=g.tenant, recipient_id=g.recipient.id,
            notification_id=g.notification.id, attempt_id=attempt.id, title="t", body=body,
        )

    async with env.uow() as uow:
        first = await uow.inbox.add(msg("original"))
        again = await uow.inbox.add(msg("redelivered"))
        await uow.commit()
        listed = await uow.inbox.list(g.tenant, g.recipient.id, state="all")
    assert again.id == first.id and again.body == "original"
    assert [m.body for m in listed] == ["original"]


async def seeded(env, n):
    """n messages for one recipient, oldest first (each on its own notification graph)."""
    g = await seed(env)
    out = []
    for i in range(n):
        g_i = await seed(env, g.tenant)
        attempt = make_attempt(g_i)
        attempt.recipient_id = g.recipient.id  # one recipient, several notifications
        async with env.uow() as uow:
            await uow.recipients.upsert_by_external_ref(g.recipient)
            await uow.commit()
        await add(env, attempt)
        async with env.uow() as uow:
            out.append(await uow.inbox.add(InboxMessage(
                id=InboxMessageId.new(), tenant_id=g.tenant, recipient_id=g.recipient.id,
                notification_id=g_i.notification.id, attempt_id=attempt.id, title=f"m{i}", body=f"body {i}",
            )))
            await uow.commit()
    return g, out


async def test_list_is_newest_first_and_pages_without_gaps_or_repeats(env):
    g, msgs = await seeded(env, 5)
    async with env.uow() as uow:
        page1 = await uow.inbox.list(g.tenant, g.recipient.id, limit=2)
        last = page1[-1]
        page2 = await uow.inbox.list(g.tenant, g.recipient.id, limit=2, before=(last.created_at, last.id))
        last = page2[-1]
        page3 = await uow.inbox.list(g.tenant, g.recipient.id, limit=2, before=(last.created_at, last.id))
    assert [m.title for m in page1 + page2 + page3] == ["m4", "m3", "m2", "m1", "m0"]


async def test_read_archive_and_unread_count(env):
    g, msgs = await seeded(env, 4)
    async with env.uow() as uow:
        assert await uow.inbox.unread_count(g.tenant, g.recipient.id) == 4
        changed = await uow.inbox.mark_read(g.tenant, g.recipient.id, [msgs[0].id], NOW)
        again = await uow.inbox.mark_read(g.tenant, g.recipient.id, [msgs[0].id], NOW)
        assert [m.id for m in changed] == [msgs[0].id] and again == []   # only newly read ones count
        assert await uow.inbox.set_archived(g.tenant, g.recipient.id, msgs[1].id, NOW)
        await uow.commit()
    async with env.uow() as uow:
        assert await uow.inbox.unread_count(g.tenant, g.recipient.id) == 2          # archived ones leave the badge
        states = {
            s: sorted(m.title for m in await uow.inbox.list(g.tenant, g.recipient.id, state=s))
            for s in ("active", "unread", "read", "archived", "all")
        }
        assert states == {
            "active": ["m0", "m2", "m3"], "unread": ["m2", "m3"], "read": ["m0"],
            "archived": ["m1"], "all": ["m0", "m1", "m2", "m3"],
        }
        assert await uow.inbox.mark_unread(g.tenant, g.recipient.id, msgs[0].id)
        assert await uow.inbox.set_archived(g.tenant, g.recipient.id, msgs[1].id, None)
        await uow.commit()
        assert await uow.inbox.unread_count(g.tenant, g.recipient.id) == 4


async def test_read_all_skips_archived_messages(env):
    g, msgs = await seeded(env, 3)
    async with env.uow() as uow:
        await uow.inbox.set_archived(g.tenant, g.recipient.id, msgs[0].id, NOW)
        changed = await uow.inbox.mark_read(g.tenant, g.recipient.id, None, NOW)
        await uow.commit()
        assert sorted(m.title for m in changed) == ["m1", "m2"]
        [archived] = await uow.inbox.list(g.tenant, g.recipient.id, state="archived")
    assert not archived.is_read


async def test_everything_is_scoped_to_tenant_and_recipient(env):
    g, msgs = await seeded(env, 2)
    other_tenant = await seed(env)
    async with env.uow() as uow:
        assert await uow.inbox.get(g.tenant, g.recipient.id, msgs[0].id) is not None
        # same message id, wrong tenant or wrong recipient: invisible and untouchable
        assert await uow.inbox.get(other_tenant.tenant, g.recipient.id, msgs[0].id) is None
        assert await uow.inbox.get(g.tenant, other_tenant.recipient.id, msgs[0].id) is None
        assert await uow.inbox.list(other_tenant.tenant, g.recipient.id, state="all") == []
        assert await uow.inbox.mark_read(other_tenant.tenant, g.recipient.id, [msgs[0].id], NOW) == []
        assert not await uow.inbox.mark_unread(g.tenant, other_tenant.recipient.id, msgs[0].id)
        assert not await uow.inbox.set_archived(other_tenant.tenant, g.recipient.id, msgs[0].id, NOW)
        assert await uow.inbox.unread_count(other_tenant.tenant, g.recipient.id) == 0


async def test_prune_drops_old_and_long_archived_messages(env):
    g, msgs = await seeded(env, 3)
    async with env.uow() as uow:
        await uow.inbox.set_archived(g.tenant, g.recipient.id, msgs[0].id, NOW - timedelta(days=30))
        await uow.inbox.set_archived(g.tenant, g.recipient.id, msgs[1].id, NOW)
        await uow.commit()
        removed = await uow.inbox.prune(NOW - timedelta(days=90), NOW - timedelta(days=7))
        await uow.commit()
        left = sorted(m.title for m in await uow.inbox.list(g.tenant, g.recipient.id, state="all"))
    assert removed == 1 and left == ["m1", "m2"]
    async with env.uow() as uow:
        assert await uow.inbox.prune(NOW + timedelta(days=1), NOW - timedelta(days=7)) == 2  # all older than "created before"
