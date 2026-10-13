"""Events, workflows, recipients, devices, preferences and dead letters."""
from __future__ import annotations


import pytest
from sqlalchemy.exc import IntegrityError

from domain.entities.dead_letter import DeadLetter
from domain.entities.device import DeviceToken
from domain.entities.event import Event
from domain.entities.preference import Preference
from domain.entities.recipient import Recipient
from domain.entities.workflow import Workflow
from domain.enums import ChannelType, DevicePlatform
from domain.value_objects.ids import (
    DeadLetterId, DeviceTokenId, EventId, PreferenceId, RecipientId, TenantId, WorkflowId,
)
from tests.repositories.conftest import make_attempt, seed


def event(tenant, key="k1"):
    return Event(id=EventId.new(), tenant_id=tenant, event_type="user.signup", source="test",
                 payload={"plan": "pro", "tags": ["a", "b"]}, idempotency_key=key)


async def test_events_roundtrip_by_idempotency_key_and_are_tenant_scoped(env):
    t1, t2 = TenantId.new(), TenantId.new()
    e = event(t1)
    async with env.uow() as uow:
        await uow.events.add(e)
        await uow.events.add(event(t2))  # same key, other tenant: fine
        await uow.commit()
        got = await uow.events.get_by_idempotency_key(t1, "k1")
        assert got.id == e.id and got.payload == e.payload
        assert (await uow.events.get_by_idempotency_key(t2, "k1")).id != e.id
        assert await uow.events.get_by_idempotency_key(t1, "nope") is None
        page, total = await uow.events.list_for_tenant(t1)
        assert total == 1 and page[0].id == e.id


async def test_the_same_idempotency_key_cannot_be_stored_twice_for_a_tenant(pg_env):
    tenant = TenantId.new()
    async with pg_env.uow() as uow:
        await uow.events.add(event(tenant, "dup"))
        await uow.commit()
    async with pg_env.uow() as uow:
        with pytest.raises(IntegrityError):
            await uow.events.add(event(tenant, "dup"))
            await uow.commit()


async def test_workflow_get_or_create_is_idempotent_and_listing_is_tenant_scoped(env):
    tenant = TenantId.new()
    w = Workflow(id=WorkflowId.new(), tenant_id=tenant, name="welcome", event_type="user.signup",
                 definition={"channels": [], "provider_chains": {"email": ["p1"]}})
    async with env.uow() as uow:
        first = await uow.workflows.get_or_create(w)
        again = await uow.workflows.get_or_create(
            Workflow(id=WorkflowId.new(), tenant_id=tenant, name="welcome", event_type="user.signup")
        )
        await uow.commit()
        assert again.id == first.id == w.id                    # the second call found the first
        assert (await uow.workflows.get(tenant, w.id)).definition["provider_chains"] == {"email": ["p1"]}
        assert (await uow.workflows.get_active_by_event_type(tenant, "user.signup")).id == w.id
        assert await uow.workflows.get_active_by_event_type(TenantId.new(), "user.signup") is None
        assert [x.id for x in await uow.workflows.list(tenant)] == [w.id]

        w.is_active = False
        await uow.workflows.update(w)
        await uow.commit()
        assert await uow.workflows.get_active_by_event_type(tenant, "user.signup") is None


async def test_recipient_upsert_keeps_identity_and_updates_contact_details(env):
    tenant = TenantId.new()
    async with env.uow() as uow:
        first = await uow.recipients.upsert_by_external_ref(
            Recipient(id=RecipientId.new(), tenant_id=tenant, external_ref="ada", email="ada@old.io"))
        second = await uow.recipients.upsert_by_external_ref(
            Recipient(id=RecipientId.new(), tenant_id=tenant, external_ref="ada", email="ada@new.io", phone="+1555"))
        await uow.commit()
        assert second.id == first.id
        got = await uow.recipients.get_by_external_ref(tenant, "ada")
        assert (got.email, got.phone) == ("ada@new.io", "+1555")
        assert await uow.recipients.get_by_external_ref(TenantId.new(), "ada") is None
        assert (await uow.recipients.get(tenant, first.id)).external_ref == "ada"


async def test_device_token_reregistration_moves_it_without_duplicating(env):
    g = await seed(env)
    other = await seed(env, g.tenant)
    async with env.uow() as uow:
        await uow.device_tokens.upsert(DeviceToken(
            id=DeviceTokenId.new(), tenant_id=g.tenant, recipient_id=g.recipient.id,
            platform=DevicePlatform.ANDROID, token="tok-1"))
        moved = await uow.device_tokens.upsert(DeviceToken(
            id=DeviceTokenId.new(), tenant_id=g.tenant, recipient_id=other.recipient.id,
            platform=DevicePlatform.IOS, token="tok-1"))
        await uow.commit()
        assert moved.recipient_id == other.recipient.id and moved.last_seen_at is not None
        assert await uow.device_tokens.list_for_recipient(g.tenant, g.recipient.id) == []
        [now_theirs] = await uow.device_tokens.list_for_recipient(g.tenant, other.recipient.id)
        assert now_theirs.platform is DevicePlatform.IOS


async def test_deleting_dead_tokens_is_tenant_scoped_and_counts(env):
    g = await seed(env)
    stranger = await seed(env)
    async with env.uow() as uow:
        for tenant, rcpt, tok in ((g.tenant, g.recipient, "dead"), (g.tenant, g.recipient, "live"),
                                  (stranger.tenant, stranger.recipient, "dead")):
            await uow.device_tokens.upsert(DeviceToken(
                id=DeviceTokenId.new(), tenant_id=tenant, recipient_id=rcpt.id,
                platform=DevicePlatform.WEB, token=tok))
        await uow.commit()
        assert await uow.device_tokens.delete_tokens(g.tenant, ["dead", "never-registered"]) == 1
        assert await uow.device_tokens.delete_tokens(g.tenant, []) == 0
        await uow.commit()
        assert [t.token for t in await uow.device_tokens.list_for_recipient(g.tenant, g.recipient.id)] == ["live"]
        # The other tenant's identical token is untouched.
        assert len(await uow.device_tokens.list_for_recipient(stranger.tenant, stranger.recipient.id)) == 1


async def test_preferences_upsert_replaces_per_channel_and_delete_is_scoped(env):
    g = await seed(env)
    async with env.uow() as uow:
        pref = Preference(id=PreferenceId.new(), tenant_id=g.tenant, recipient_id=g.recipient.id,
                          channel=ChannelType.SMS, opted_in=True)
        await uow.preferences.upsert(pref)
        await uow.preferences.upsert(Preference(
            id=PreferenceId.new(), tenant_id=g.tenant, recipient_id=g.recipient.id,
            channel=ChannelType.SMS, opted_in=False))
        await uow.commit()
        [only] = await uow.preferences.list_for_recipient(g.tenant, g.recipient.id)
        assert only.channel is ChannelType.SMS and only.opted_in is False   # one row per channel
        assert not await uow.preferences.delete(TenantId.new(), only.id)    # other tenant can't delete it
        assert await uow.preferences.delete(g.tenant, only.id)


async def test_dead_letters_page_newest_first_within_a_tenant(env):
    g = await seed(env)
    stranger = await seed(env)
    attempts = []
    for gx in (g, await seed(env, g.tenant)):
        a = make_attempt(gx)
        attempts.append((gx, a))
    stranger_attempt = make_attempt(stranger)
    async with env.uow() as uow:
        await uow.delivery_attempts.add_many([a for _, a in attempts] + [stranger_attempt])
        await uow.commit()
        made = []
        for gx, a in attempts:
            d = DeadLetter(id=DeadLetterId.new(), tenant_id=g.tenant, attempt_id=a.id,
                           notification_id=gx.notification.id, channel=a.channel, reason=f"r-{a.id}")
            await uow.dead_letters.add(d)
            await uow.commit()  # distinct commit times keep "newest first" unambiguous
            made.append(d)
        await uow.dead_letters.add(DeadLetter(
            id=DeadLetterId.new(), tenant_id=stranger.tenant, attempt_id=stranger_attempt.id,
            notification_id=stranger.notification.id, channel=stranger_attempt.channel, reason="theirs"))
        await uow.commit()

        page, total = await uow.dead_letters.list_for_tenant(g.tenant)
        assert total == 2 and [d.id for d in page] == [made[1].id, made[0].id]
        assert (await uow.dead_letters.get(g.tenant, made[0].id)).reason == made[0].reason
        assert await uow.dead_letters.get(stranger.tenant, made[0].id) is None


async def test_concurrent_first_events_for_a_new_recipient_do_not_collide(pg_env):
    """Two ingests creating the same brand-new recipient at once must both succeed (was a 409)."""
    import asyncio

    from domain.entities.recipient import Recipient
    from domain.value_objects.ids import RecipientId, TenantId

    tenant = TenantId.new()

    async def upsert(email):
        async with pg_env.uow() as uow:
            r = await uow.recipients.upsert_by_external_ref(
                Recipient(id=RecipientId.new(), tenant_id=tenant, external_ref="newcomer", email=email)
            )
            await uow.commit()
            return r

    results = await asyncio.gather(*(upsert(f"e{i}@example.com") for i in range(10)))
    assert len({r.id for r in results}) == 1            # everyone got the same recipient
    async with pg_env.uow() as uow:
        stored = await uow.recipients.get_by_external_ref(tenant, "newcomer")
    assert stored.id == results[0].id and stored.email.endswith("@example.com")
