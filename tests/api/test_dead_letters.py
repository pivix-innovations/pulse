from __future__ import annotations

from datetime import datetime, timezone

from domain.entities.dead_letter import DeadLetter
from domain.entities.delivery import DeliveryAttempt
from domain.entities.notification import Notification
from domain.enums import ChannelType, DeliveryStatus, NotificationStatus
from domain.value_objects.ids import (
    DeadLetterId,
    DeliveryAttemptId,
    EventId,
    NotificationId,
    RecipientId,
    WorkflowId,
)


def seed_dead(world):
    notification = Notification(
        id=NotificationId.new(),
        tenant_id=world.tenant_id,
        event_id=EventId.new(),
        workflow_id=WorkflowId.new(),
        status=NotificationStatus.FAILED,
    )
    world.uow.notifications.items[str(notification.id)] = notification
    attempt = DeliveryAttempt(
        id=DeliveryAttemptId.new(),
        tenant_id=world.tenant_id,
        notification_id=notification.id,
        recipient_id=RecipientId.new(),
        channel=ChannelType.EMAIL,
    )
    attempt.mark_sent(datetime.now(timezone.utc))
    attempt.mark_dead("never confirmed")
    world.uow.delivery_attempts.items[str(attempt.id)] = attempt
    dead = DeadLetter(
        id=DeadLetterId.new(),
        tenant_id=world.tenant_id,
        attempt_id=attempt.id,
        notification_id=notification.id,
        channel=ChannelType.EMAIL,
        reason="never confirmed",
    )
    world.uow.dead_letters.items.append(dead)
    return notification, attempt, dead


async def test_list_dead_letters(world):
    _, _, dead = seed_dead(world)

    res = await world.client.get("/api/v1/dead-letters", headers=world.key_headers)

    assert res.status_code == 200
    assert res.headers["X-Total-Count"] == "1"
    assert res.json()[0]["id"] == str(dead.id)
    assert res.json()[0]["reason"] == "never confirmed"


async def test_retry_resets_attempt_and_republishes_via_outbox(world):
    notification, attempt, dead = seed_dead(world)

    res = await world.client.post(
        f"/api/v1/dead-letters/{dead.id}/retry", headers=world.key_headers
    )

    assert res.status_code == 202
    assert attempt.status is DeliveryStatus.PENDING
    assert attempt.attempt_count == 0
    assert notification.status is NotificationStatus.PROCESSING
    entries = list(world.uow.outbox.items.values())
    assert [e.topic for e in entries] == ["deliver.email"]
    assert entries[0].payload["attempt_id"] == str(attempt.id)
    assert world.redis.deleted == [f"pulse:dispatch:{attempt.idempotency_key}"]

    # Already redriven: no longer dead.
    again = await world.client.post(
        f"/api/v1/dead-letters/{dead.id}/retry", headers=world.key_headers
    )
    assert again.status_code == 409


async def test_retry_unknown_dead_letter_is_404(world):
    res = await world.client.post(
        "/api/v1/dead-letters/00000000-0000-0000-0000-000000000009/retry",
        headers=world.key_headers,
    )
    assert res.status_code == 404
