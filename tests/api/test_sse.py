from __future__ import annotations

import contextlib

import pytest


class _StopStream(Exception):
    pass


class _ExplodingRegistry:
    """Stops the otherwise endless stream right after the auth + provisioning phase."""

    @contextlib.asynccontextmanager
    async def subscribe(self, tenant_id, recipient_id):
        raise _StopStream
        yield  # pragma: no cover


async def test_stream_requires_credentials(world):
    res = await world.client.get("/api/v1/sse/stream", params={"recipient": "u1"})
    assert res.status_code == 401

    bad = await world.client.get(
        "/api/v1/sse/stream", params={"recipient": "u1"}, headers={"X-API-Key": "pk_wrong"}
    )
    assert bad.status_code == 401


async def test_stream_auto_provisions_unknown_recipient(world):
    world.client._transport.app.state.sse_registry = _ExplodingRegistry()
    assert world.uow.recipients.items == {}

    with pytest.raises(_StopStream):
        async with world.client.stream(
            "GET", "/api/v1/sse/stream", params={"recipient": "brand-new"}, headers=world.key_headers
        ) as res:
            await res.aread()

    refs = [r.external_ref for r in world.uow.recipients.items.values()]
    assert refs == ["brand-new"]


# ── which notifications a (re)connecting client is replayed ────────────


async def _seed_attempts(world, recipient_ref="ada"):
    """One unacknowledged in-app attempt for `ada`, plus decoys that must never be replayed."""
    from datetime import datetime, timezone

    from domain.entities.delivery import DeliveryAttempt
    from domain.entities.notification import Notification
    from domain.entities.recipient import Recipient
    from domain.enums import ChannelType
    from domain.value_objects.ids import (
        DeliveryAttemptId, EventId, NotificationId, RecipientId, WorkflowId,
    )

    now = datetime.now(timezone.utc)
    ada = Recipient(id=RecipientId.new(), tenant_id=world.tenant_id, external_ref=recipient_ref)
    bob = Recipient(id=RecipientId.new(), tenant_id=world.tenant_id, external_ref="bob")
    for r in (ada, bob):
        world.uow.recipients.items[str(r.id)] = r

    def attempt(recipient, channel, *, sent=True, delivered=False):
        n = Notification(id=NotificationId.new(), tenant_id=world.tenant_id,
                         event_id=EventId.new(), workflow_id=WorkflowId.new())
        world.uow.notifications.items[str(n.id)] = n
        a = DeliveryAttempt(id=DeliveryAttemptId.new(), tenant_id=world.tenant_id, notification_id=n.id,
                            recipient_id=recipient.id, channel=channel, subject="s", body="b")
        if sent:
            a.mark_sent(now)
        if delivered:
            a.mark_delivered(now)
        world.uow.delivery_attempts.items[str(a.id)] = a
        return a

    return {
        "missed": attempt(ada, ChannelType.IN_APP),                       # sent, never acknowledged
        "acked": attempt(ada, ChannelType.IN_APP, delivered=True),        # already delivered
        "not_sent": attempt(ada, ChannelType.IN_APP, sent=False),
        "email": attempt(ada, ChannelType.EMAIL),                         # not an in-app notification
        "someone_else": attempt(bob, ChannelType.IN_APP),
    }


async def _replayed_ids(world, monkeypatch, *, headers=None, params=None):
    import api.v1.sse as sse

    captured = {}

    async def fake_stream(*, replay_attempts, subscribe, is_disconnected, heartbeat_seconds=0):
        captured["ids"] = [str(a.id) for a in replay_attempts]
        yield "data: {}\n\n"

    monkeypatch.setattr(sse, "stream_events", fake_stream)
    world.app.state.sse_registry = _ExplodingRegistry()   # never entered: the stream body is stubbed
    res = await world.client.get(
        "/api/v1/sse/stream", params={"recipient": "ada", **(params or {})},
        headers={**world.key_headers, **(headers or {})},
    )
    assert res.status_code == 200
    return res, captured["ids"]


async def test_reconnecting_with_last_event_id_replays_only_that_recipients_unacknowledged_in_app_attempts(
    world, monkeypatch
):
    seeded = await _seed_attempts(world)
    res, ids = await _replayed_ids(world, monkeypatch, headers={"Last-Event-ID": "whatever"})
    assert ids == [str(seeded["missed"].id)]
    assert res.headers["content-type"].startswith("text/event-stream")
    assert res.headers["cache-control"] == "no-cache" and res.headers["x-accel-buffering"] == "no"


async def test_the_replay_query_flag_does_the_same(world, monkeypatch):
    seeded = await _seed_attempts(world)
    _, ids = await _replayed_ids(world, monkeypatch, params={"replay": "true"})
    assert ids == [str(seeded["missed"].id)]


async def test_a_first_connection_replays_nothing(world, monkeypatch):
    await _seed_attempts(world)
    _, ids = await _replayed_ids(world, monkeypatch)
    assert ids == []


async def test_acknowledging_removes_it_from_future_replays(world, monkeypatch):
    seeded = await _seed_attempts(world)
    missed = seeded["missed"]
    ack = await world.client.post(
        f"/api/v1/notifications/{missed.notification_id}/ack",
        json={"attempt_id": str(missed.id)}, headers=world.key_headers,
    )
    assert ack.status_code == 200, ack.text
    _, ids = await _replayed_ids(world, monkeypatch, headers={"Last-Event-ID": "x"})
    assert ids == []
