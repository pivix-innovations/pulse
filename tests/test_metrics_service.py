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
from services.metrics_service import MetricsService, bucket_for
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


def test_bucket_width_keeps_every_window_to_a_readable_number_of_bars():
    assert [bucket_for(h) for h in (1, 6, 24, 168, 720)] == [60, 900, 1800, 21600, 86400]
    assert all(h * 3600 / bucket_for(h) <= 60 for h in (1, 2, 6, 24, 72, 168, 720))


async def test_timeline_counts_attempts_per_bucket_and_status():
    uow = InMemoryUnitOfWork()
    tenant = TenantId.new()
    now = datetime.now(timezone.utc)

    def attempt(created, status_fn=None, tenant_id=tenant):
        a = DeliveryAttempt(id=DeliveryAttemptId.new(), tenant_id=tenant_id, notification_id=NotificationId.new(),
                            recipient_id=RecipientId.new(), channel=ChannelType.EMAIL, created_at=created)
        if status_fn:
            status_fn(a)
        uow.delivery_attempts.items[str(a.id)] = a

    attempt(now - timedelta(minutes=2), lambda a: (a.mark_sent(now), a.mark_delivered(now)))
    attempt(now - timedelta(minutes=2))                                   # pending, same bucket
    attempt(now - timedelta(hours=5))                                     # an earlier bucket
    attempt(now - timedelta(hours=30))                                    # outside a 24h window
    attempt(now, tenant_id=TenantId.new())                                # another tenant

    summary = await MetricsService(lambda: uow).summary(tenant, window_hours=24)
    assert summary.bucket_seconds == 1800
    starts = [b for b, _ in summary.timeline]
    assert starts == sorted(starts) and len(starts) == 2
    assert all(b.timestamp() % 1800 == 0 for b in starts)
    assert sum(sum(c.values()) for _, c in summary.timeline) == 3
    recent = summary.timeline[-1][1]
    assert recent == {"delivered": 1, "pending": 1}
