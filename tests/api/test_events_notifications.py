from __future__ import annotations

from datetime import datetime, timezone

from domain.enums import DeliveryStatus, NotificationStatus

EVENT = {"event_type": "user.signup", "source": "quotient", "payload": {"plan": "pro"}}


async def test_ingest_event_then_lookup_and_list(world):
    res = await world.client.post(
        "/api/v1/events", json={**EVENT, "idempotency_key": "k1"}, headers=world.key_headers
    )
    assert res.status_code == 202
    nid = res.json()["notification_id"]

    detail = await world.client.get(f"/api/v1/notifications/{nid}", headers=world.key_headers)
    assert detail.status_code == 200
    assert detail.json()["status"] == "processing"
    assert {a["channel"] for a in detail.json()["attempts"]} == {"email", "in_app", "push"}

    listing = await world.client.get("/api/v1/notifications", headers=world.key_headers)
    assert listing.headers["X-Total-Count"] == "1"
    assert [n["id"] for n in listing.json()] == [nid]

    filtered = await world.client.get(
        "/api/v1/notifications?status=failed", headers=world.key_headers
    )
    assert filtered.json() == [] and filtered.headers["X-Total-Count"] == "0"

    events = await world.client.get("/api/v1/events", headers=world.key_headers)
    assert events.headers["X-Total-Count"] == "1"
    assert events.json()[0]["idempotency_key"] == "k1"


async def test_ingest_is_idempotent_and_validates(world):
    first = await world.client.post(
        "/api/v1/events", json={**EVENT, "idempotency_key": "same"}, headers=world.key_headers
    )
    second = await world.client.post(
        "/api/v1/events", json={**EVENT, "idempotency_key": "same"}, headers=world.key_headers
    )
    assert first.json()["notification_id"] == second.json()["notification_id"]

    unknown = await world.client.post(
        "/api/v1/events", json={**EVENT, "event_type": "nope"}, headers=world.key_headers
    )
    assert unknown.status_code == 422
    invalid = await world.client.post("/api/v1/events", json={}, headers=world.key_headers)
    assert invalid.status_code == 422


async def test_ack_marks_delivered_and_settles_notification(world):
    nid = (
        await world.client.post("/api/v1/events", json=EVENT, headers=world.key_headers)
    ).json()["notification_id"]
    attempts = list(world.uow.delivery_attempts.items.values())
    for a in attempts:
        a.mark_sent(datetime.now(timezone.utc))

    acked = []
    for a in attempts:
        res = await world.client.post(
            f"/api/v1/notifications/{nid}/ack",
            json={"attempt_id": str(a.id)},
            headers=world.key_headers,
        )
        assert res.status_code == 200
        acked.append(res.json()["status"])

    assert set(acked) == {DeliveryStatus.DELIVERED.value}
    notification = next(iter(world.uow.notifications.items.values()))
    assert notification.status is NotificationStatus.COMPLETED


async def test_unknown_notification_is_404(world):
    res = await world.client.get(
        "/api/v1/notifications/00000000-0000-0000-0000-000000000009", headers=world.key_headers
    )
    assert res.status_code == 404


async def test_an_unavailable_recipient_directory_is_a_503_to_retry_not_a_422(world):
    from services.ports.clients import SentinelUnavailable

    class Down:
        async def resolve_recipients(self, *a):
            raise SentinelUnavailable("down")

    world.app.state.notification_service._sentinel = Down()
    res = await world.client.post(
        "/api/v1/events", json={**EVENT, "idempotency_key": "k-down"}, headers=world.key_headers
    )
    assert res.status_code == 503 and res.headers["Retry-After"] == "5"
    assert "recipient directory" in res.json()["detail"]
