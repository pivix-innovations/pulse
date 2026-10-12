from __future__ import annotations

from datetime import datetime, timedelta, timezone

from config.reconciliation import ReconciliationSettings
from domain.entities.delivery import DeliveryAttempt
from domain.entities.notification import Notification
from domain.entities.recipient import Recipient
from domain.entities.workflow import Workflow
from domain.enums import ChannelType, DeliveryStatus, NotificationStatus, OutboxStatus
from domain.value_objects.ids import (
    DeliveryAttemptId,
    EventId,
    NotificationId,
    RecipientId,
    TenantId,
    WorkflowId,
)
from services.reconciliation_service import ReconciliationService
from services.rendering_service import RenderingService
from tests.fakes import InMemoryUnitOfWork
from workflows.registry import WorkflowRegistry


def seed_world(uow: InMemoryUnitOfWork, tenant: TenantId):
    """Recipient + workflow (welcome definition) + notification."""
    definition = WorkflowRegistry().get("user.signup")
    workflow = Workflow(
        id=WorkflowId.new(),
        tenant_id=tenant,
        name=definition.name,
        event_type=definition.event_type,
        definition=definition.as_dict(),
    )
    uow.workflows.items[f"{tenant}:{workflow.event_type}:{workflow.name}"] = workflow
    recipient = Recipient(
        id=RecipientId.new(),
        tenant_id=tenant,
        external_ref="u1",
        email="u1@example.com",
        phone="+15550001111",
    )
    uow.recipients.items[str(recipient.id)] = recipient
    notification = Notification(
        id=NotificationId.new(),
        tenant_id=tenant,
        event_id=EventId.new(),
        workflow_id=workflow.id,
        payload={"plan": "pro"},
    )
    uow.notifications.items[str(notification.id)] = notification
    return workflow, recipient, notification


def seed_attempt(
    uow, tenant, notification, recipient, channel, status: DeliveryStatus
) -> DeliveryAttempt:
    attempt = DeliveryAttempt(
        id=DeliveryAttemptId.new(),
        tenant_id=tenant,
        notification_id=notification.id,
        recipient_id=recipient.id,
        channel=channel,
        subject="hi",
        body="hello",
    )
    if status is DeliveryStatus.SENT:
        attempt.mark_sent(datetime.now(timezone.utc) - timedelta(hours=2))
    elif status is DeliveryStatus.FAILED:
        attempt.mark_failed("No live SSE connection for recipient")
        attempt.updated_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    uow.delivery_attempts.items[str(attempt.id)] = attempt
    return attempt


def make_sweep(uow) -> ReconciliationService:
    return ReconciliationService(lambda: uow, RenderingService(), ReconciliationSettings())


async def test_stuck_sent_push_escalates_to_sms():
    uow = InMemoryUnitOfWork()
    tenant = TenantId.new()
    _, recipient, notification = seed_world(uow, tenant)
    push = seed_attempt(uow, tenant, notification, recipient, ChannelType.PUSH, DeliveryStatus.SENT)

    handled = await make_sweep(uow).sweep_once()

    assert handled == 1
    assert push.status is DeliveryStatus.DEAD  # retired after escalation
    sms = await uow.delivery_attempts.get_by_dispatch(
        notification.id, recipient.id, ChannelType.SMS
    )
    assert sms is not None and sms.status is DeliveryStatus.PENDING
    assert "u1" in sms.body  # rendered from the fallback template
    outbox = list(uow.outbox.items.values())
    assert len(outbox) == 1
    assert outbox[0].topic == "deliver.sms"
    assert outbox[0].status is OutboxStatus.PENDING
    assert uow.dead_letters.items == []  # escalated, not dead-lettered


async def test_failed_in_app_escalates_to_push():
    uow = InMemoryUnitOfWork()
    tenant = TenantId.new()
    _, recipient, notification = seed_world(uow, tenant)
    in_app = seed_attempt(
        uow, tenant, notification, recipient, ChannelType.IN_APP, DeliveryStatus.FAILED
    )

    await make_sweep(uow).sweep_once()

    assert in_app.status is DeliveryStatus.DEAD
    push = await uow.delivery_attempts.get_by_dispatch(
        notification.id, recipient.id, ChannelType.PUSH
    )
    assert push is not None and push.status is DeliveryStatus.PENDING


async def test_escalation_capped_when_fallback_channel_already_attempted():
    uow = InMemoryUnitOfWork()
    tenant = TenantId.new()
    _, recipient, notification = seed_world(uow, tenant)
    in_app = seed_attempt(
        uow, tenant, notification, recipient, ChannelType.IN_APP, DeliveryStatus.FAILED
    )
    # A push attempt already exists for this (notification, recipient).
    seed_attempt(uow, tenant, notification, recipient, ChannelType.PUSH, DeliveryStatus.SENT)

    await make_sweep(uow).sweep_once()

    assert in_app.status is DeliveryStatus.DEAD
    assert any("already attempted" in d.reason for d in uow.dead_letters.items)


async def test_unconfirmed_email_without_fallback_dead_letters():
    uow = InMemoryUnitOfWork()
    tenant = TenantId.new()
    _, recipient, notification = seed_world(uow, tenant)
    email = seed_attempt(
        uow, tenant, notification, recipient, ChannelType.EMAIL, DeliveryStatus.SENT
    )

    await make_sweep(uow).sweep_once()

    assert email.status is DeliveryStatus.DEAD
    assert len(uow.dead_letters.items) == 1
    assert "past TTL" in uow.dead_letters.items[0].reason


async def test_fresh_sent_attempts_are_left_alone():
    uow = InMemoryUnitOfWork()
    tenant = TenantId.new()
    _, recipient, notification = seed_world(uow, tenant)
    push = seed_attempt(uow, tenant, notification, recipient, ChannelType.PUSH, DeliveryStatus.SENT)
    push.sent_at = datetime.now(timezone.utc)  # inside the TTL window

    handled = await make_sweep(uow).sweep_once()

    assert handled == 0
    assert push.status is DeliveryStatus.SENT


async def test_delivered_attempts_are_never_touched():
    uow = InMemoryUnitOfWork()
    tenant = TenantId.new()
    _, recipient, notification = seed_world(uow, tenant)
    attempt = seed_attempt(
        uow, tenant, notification, recipient, ChannelType.PUSH, DeliveryStatus.SENT
    )
    attempt.mark_delivered(datetime.now(timezone.utc))

    handled = await make_sweep(uow).sweep_once()

    assert handled == 0
    assert attempt.status is DeliveryStatus.DELIVERED


async def test_non_confirming_sent_attempt_is_never_swept():
    uow = InMemoryUnitOfWork()
    tenant = TenantId.new()
    _, recipient, notification = seed_world(uow, tenant)
    attempt = seed_attempt(uow, tenant, notification, recipient, ChannelType.EMAIL, DeliveryStatus.PENDING)
    attempt.mark_sent(datetime.now(timezone.utc) - timedelta(hours=5), expects_confirmation=False)

    await make_sweep(uow).sweep_once()

    assert attempt.status is DeliveryStatus.SENT
    assert not uow.dead_letters.items


async def test_sweep_settles_notification_once_attempts_are_terminal():
    uow = InMemoryUnitOfWork()
    tenant = TenantId.new()
    _, recipient, notification = seed_world(uow, tenant)
    notification.mark_processing()
    attempt = seed_attempt(uow, tenant, notification, recipient, ChannelType.EMAIL, DeliveryStatus.PENDING)
    attempt.mark_sent(datetime.now(timezone.utc), expects_confirmation=False)

    await make_sweep(uow).sweep_once()

    assert notification.status is NotificationStatus.COMPLETED


async def test_sweep_fails_notification_when_every_attempt_died():
    uow = InMemoryUnitOfWork()
    tenant = TenantId.new()
    _, recipient, notification = seed_world(uow, tenant)
    notification.mark_processing()
    attempt = seed_attempt(uow, tenant, notification, recipient, ChannelType.EMAIL, DeliveryStatus.PENDING)
    attempt.mark_failed("boom")
    attempt.mark_dead("boom")

    await make_sweep(uow).sweep_once()

    assert notification.status is NotificationStatus.FAILED


async def test_sweep_requeues_stuck_in_doubt_attempt():
    uow = InMemoryUnitOfWork()
    tenant = TenantId.new()
    _, recipient, notification = seed_world(uow, tenant)
    attempt = seed_attempt(uow, tenant, notification, recipient, ChannelType.EMAIL, DeliveryStatus.PENDING)
    attempt.mark_dispatching(datetime.now(timezone.utc) - timedelta(hours=1))

    handled = await make_sweep(uow).sweep_once()

    assert handled == 1
    assert attempt.dispatch_started_at is None  # claim cleared; clock restarted
    assert attempt.status is DeliveryStatus.PENDING
    topics = [e.topic for e in uow.outbox.items.values()]
    assert topics == ["deliver.email"]
