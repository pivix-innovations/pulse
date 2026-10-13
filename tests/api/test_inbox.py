"""The recipient inbox API, driven end to end through ingest -> delivery -> read."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from config.retry import RetrySettings
from domain.enums import ChannelType, DeliveryStatus, NotificationStatus
from services.delivery_service import DeliveryService
from tests.fakes import FakeDispatchGuard, FakeProvider, FakeProviderRegistry, FakePublisher

INBOX = "/api/v1/inbox"
EVENT = {"event_type": "user.signup", "source": "quotient", "payload": {"plan": "pro"}}


async def deliver_in_app(world, key="k1"):
    """Ingest an event for u1 and run its in-app attempt through the delivery service."""
    r = await world.client.post("/api/v1/events", headers=world.key_headers, json={**EVENT, "idempotency_key": key})
    assert r.status_code == 202, r.text
    nid = r.json()["notification_id"]
    attempts = [a for a in world.uow.delivery_attempts.items.values() if str(a.notification_id) == nid]
    attempt = next(a for a in attempts if a.channel is ChannelType.IN_APP)
    service = DeliveryService(
        uow_factory=lambda: world.uow,
        providers=FakeProviderRegistry(FakeProvider(ChannelType.IN_APP)),
        guard=FakeDispatchGuard(), publisher=FakePublisher(), retry=RetrySettings(),
    )
    assert await service.deliver(world.tenant_id, attempt.id) == "sent"
    return nid, attempt, attempts


def get(world, path="", **params):
    return world.client.get(INBOX + path, params={"recipient": "u1", **params}, headers=world.key_headers)


def post(world, path, **params):
    return world.client.post(INBOX + path, params={"recipient": "u1", **params}, headers=world.key_headers)


async def test_a_delivered_in_app_message_shows_up_unread_with_a_badge_count(world):
    nid, attempt, _ = await deliver_in_app(world)
    page = (await get(world)).json()
    assert page["unread_count"] == 1 and page["next_cursor"] is None
    [m] = page["items"]
    assert m["notification_id"] == nid and m["is_read"] is False and m["is_archived"] is False
    assert m["title"] and m["body"] and m["created_at"]
    assert (await get(world, "/unread-count")).json() == {"unread_count": 1}


async def test_an_unknown_recipient_has_an_empty_inbox_not_an_error(world):
    res = await world.client.get(INBOX, params={"recipient": "nobody-yet"}, headers=world.key_headers)
    assert res.status_code == 200
    assert res.json() == {"items": [], "next_cursor": None, "unread_count": 0}


async def test_reading_a_message_confirms_delivery_and_settles_the_notification(world):
    nid, attempt, attempts = await deliver_in_app(world)
    assert attempt.status is DeliveryStatus.SENT
    [m] = (await get(world)).json()["items"]

    res = await post(world, f"/{m['id']}/read")
    assert res.status_code == 200 and res.json() == {"changed": 1, "unread_count": 0}

    assert attempt.status is DeliveryStatus.DELIVERED and attempt.delivered_at is not None
    again = await post(world, f"/{m['id']}/read")           # idempotent
    assert again.json()["changed"] == 0
    [m] = (await get(world, state="read")).json()["items"]
    assert m["is_read"] is True and m["read_at"]


async def test_reading_the_last_open_attempt_completes_the_notification(world):
    nid, attempt, attempts = await deliver_in_app(world)
    for other in attempts:                       # every other channel is already done
        if other is not attempt:
            other.mark_sent(datetime.now(timezone.utc), expects_confirmation=False)
    notification = world.uow.notifications.items[nid]
    notification.status = NotificationStatus.PROCESSING
    [m] = (await get(world)).json()["items"]
    await post(world, f"/{m['id']}/read")
    assert world.uow.notifications.items[nid].status is NotificationStatus.COMPLETED


async def test_unread_archive_unarchive_and_read_all(world):
    for i in range(3):
        await deliver_in_app(world, key=f"k{i}")
    items = (await get(world)).json()["items"]
    assert len(items) == 3

    assert (await post(world, f"/{items[0]['id']}/archive")).json()["unread_count"] == 2
    assert {m["id"] for m in (await get(world)).json()["items"]} == {items[1]["id"], items[2]["id"]}
    assert [m["id"] for m in (await get(world, state="archived")).json()["items"]] == [items[0]["id"]]
    assert len((await get(world, state="all")).json()["items"]) == 3

    res = await post(world, "/read-all")
    assert res.json() == {"changed": 2, "unread_count": 0}      # the archived one is left alone
    assert (await post(world, f"/{items[1]['id']}/unread")).json()["unread_count"] == 1
    assert (await post(world, f"/{items[0]['id']}/unarchive")).json()["unread_count"] == 2


async def test_cursor_paging_walks_the_whole_inbox_newest_first(world):
    for i in range(5):
        await deliver_in_app(world, key=f"p{i}")
    seen, cursor = [], None
    while True:
        page = (await get(world, limit=2, **({"cursor": cursor} if cursor else {}))).json()
        seen += [m["id"] for m in page["items"]]
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert len(seen) == len(set(seen)) == 5
    full = [m["id"] for m in (await get(world, limit=100)).json()["items"]]
    assert seen == full


@pytest.mark.parametrize("params, status", [({"state": "bogus"}, 422), ({"cursor": "not-a-cursor"}, 422), ({"limit": 0}, 422)])
async def test_bad_query_parameters_are_rejected(world, params, status):
    assert (await get(world, **params)).status_code == status


async def test_a_message_that_does_not_exist_is_a_404(world):
    await deliver_in_app(world)
    ghost = "7b7f6a1c-0000-4000-8000-000000000000"
    for action in ("read", "unread", "archive", "unarchive"):
        assert (await post(world, f"/{ghost}/{action}")).status_code == 404, action
    unknown = await world.client.post(
        f"{INBOX}/{ghost}/read", params={"recipient": "never-seen"}, headers=world.key_headers
    )
    assert unknown.status_code == 404


async def test_the_inbox_requires_the_client_scope(world):
    from services.identity_service import IdentityService

    identity = IdentityService(lambda: world.uow)
    ingest_only, _ = await identity.create_api_key(world.tenant_id, "producer", scopes=["ingest"])
    reader, _ = await identity.create_api_key(world.tenant_id, "reader", scopes=["read"])
    client_key, _ = await identity.create_api_key(world.tenant_id, "app", scopes=["client"])

    def req(key):
        return world.client.get(INBOX, params={"recipient": "u1"}, headers={"X-API-Key": key})

    assert (await req(ingest_only)).status_code == 403
    assert (await req(reader)).status_code == 403
    assert (await req(client_key)).status_code == 200
    assert (await world.client.get(INBOX, params={"recipient": "u1"})).status_code == 401
