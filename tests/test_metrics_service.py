from __future__ import annotations

from datetime import datetime, timedelta, timezone

from domain.entities.dead_letter import DeadLetter
from domain.entities.delivery import DeliveryAttempt
from domain.entities.notification import Notification
from domain.enums import ChannelType
from domain.value_objects.ids import (
    DeadLetterId,
    DeliveryAttemptId,
    EventId,
    NotificationId,
    RecipientId,
    TenantId,
    WorkflowId,
)
from services.metrics_service import MetricsService
from tests.fakes import InMemoryUnitOfWork


async def test_summary_aggregates_by_channel_status_and_latency():
    uow = InMemoryUnitOfWork()
    tenant = TenantId.new()
    other_tenant = TenantId.new()
    now = datetime.now(timezone.utc)

    notification = Notification(
        id=NotificationId.new(),
        tenant_id=tenant,
        event_id=EventId.new(),
        workflow_id=WorkflowId.new(),
    )
    notification.mark_processing()
    uow.notifications.items[str(notification.id)] = notification

    def attempt(channel, tenant_id=tenant) -> DeliveryAttempt:
        a = DeliveryAttempt(
            id=DeliveryAttemptId.new(),
            tenant_id=tenant_id,
            notification_id=notification.id,
            recipient_id=RecipientId.new(),
            channel=channel,
        )
        uow.delivery_attempts.items[str(a.id)] = a
        return a

    delivered = attempt(ChannelType.EMAIL)
    delivered.mark_sent(now - timedelta(seconds=10))
    delivered.mark_delivered(now)  # 10s latency
    attempt(ChannelType.EMAIL).mark_sent(now)
    attempt(ChannelType.SMS)  # pending
    attempt(ChannelType.PUSH, tenant_id=other_tenant)  # other tenant: excluded

    uow.dead_letters.items.append(
        DeadLetter(
            id=DeadLetterId.new(),
            tenant_id=tenant,
            attempt_id=DeliveryAttemptId.new(),
            notification_id=notification.id,
            channel=ChannelType.PUSH,
            reason="test",
        )
    )

    summary = await MetricsService(lambda: uow).summary(tenant, window_hours=24)

    assert summary.notifications == {"processing": 1}
    assert summary.attempts["email"] == {"delivered": 1, "sent": 1}
    assert summary.attempts["sms"] == {"pending": 1}
    assert "push" not in summary.attempts  # tenant isolation
    assert summary.dead_letters == 1
    assert abs(summary.avg_confirmation_seconds - 10.0) < 0.5


async def test_summary_empty_tenant():
    summary = await MetricsService(lambda: InMemoryUnitOfWork()).summary(TenantId.new())
    assert summary.notifications == {}
    assert summary.attempts == {}
    assert summary.dead_letters == 0
    assert summary.avg_confirmation_seconds is None
