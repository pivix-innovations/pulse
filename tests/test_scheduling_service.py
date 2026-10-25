from __future__ import annotations

from datetime import datetime, timedelta, timezone

from domain.enums import NotificationStatus, OutboxStatus
from domain.value_objects.ids import TenantId
from services.notification_service import IncomingEvent, NotificationService
from services.ports.clients import ResolvedRecipient
from services.rendering_service import RenderingService
from services.scheduling_service import SchedulingService
from tests.fakes import InMemoryUnitOfWork, StaticSentinelClient
from workflows.registry import WorkflowRegistry


def make_services(uow: InMemoryUnitOfWork, recipients: list[ResolvedRecipient]):
    sentinel = StaticSentinelClient(recipients)
    notifications = NotificationService(
        uow_factory=lambda: uow,
        sentinel=sentinel,
        registry=WorkflowRegistry(),
        rendering=RenderingService(),
    )
    scheduler = SchedulingService(
        uow_factory=lambda: uow, notifications=notifications, sentinel=sentinel
    )
    return notifications, scheduler


async def test_future_event_defers_fan_out():
    uow = InMemoryUnitOfWork()
    tenant = TenantId.new()
    notifications, scheduler = make_services(
        uow, [ResolvedRecipient(external_ref="u1", email="u1@example.com")]
    )

    nid = await notifications.process_event(
        tenant,
        IncomingEvent(
            event_type="user.signup",
            source="quotient",
            scheduled_at=datetime.now(timezone.utc) + timedelta(hours=1),
        ),
    )

    notification = await uow.notifications.get(tenant, nid)
    assert notification.status is NotificationStatus.CREATED
    assert await uow.delivery_attempts.list_for_notification(tenant, nid) == []
    assert uow.outbox.items == {}

    # Not due yet: the scheduler leaves it alone.
    assert await scheduler.dispatch_due_once() == 0
    assert notification.status is NotificationStatus.CREATED


async def test_due_notification_fans_out():
    uow = InMemoryUnitOfWork()
    tenant = TenantId.new()
    notifications, scheduler = make_services(
        uow, [ResolvedRecipient(external_ref="u1", email="u1@example.com")]
    )

    nid = await notifications.process_event(
        tenant,
        IncomingEvent(
            event_type="user.signup",
            source="quotient",
            payload={"plan": "pro"},
            scheduled_at=datetime.now(timezone.utc) + timedelta(hours=1),
        ),
    )
    # Time passes...
    notification = await uow.notifications.get(tenant, nid)
    notification.scheduled_at = datetime.now(timezone.utc) - timedelta(seconds=1)

    dispatched = await scheduler.dispatch_due_once()

    assert dispatched == 1
    assert notification.status is NotificationStatus.PROCESSING
    attempts = await uow.delivery_attempts.list_for_notification(tenant, nid)
    assert len(attempts) == 3  # email + in_app + push from the welcome workflow
    assert all(e.status is OutboxStatus.PENDING for e in uow.outbox.items.values())
    assert len(uow.outbox.items) == 3

    # A second pass must not double-dispatch.
    assert await scheduler.dispatch_due_once() == 0
    assert len(await uow.delivery_attempts.list_for_notification(tenant, nid)) == 3


async def test_past_scheduled_at_fans_out_immediately():
    uow = InMemoryUnitOfWork()
    tenant = TenantId.new()
    notifications, _ = make_services(
        uow, [ResolvedRecipient(external_ref="u1", email="u1@example.com")]
    )

    nid = await notifications.process_event(
        tenant,
        IncomingEvent(
            event_type="user.signup",
            source="quotient",
            scheduled_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        ),
    )

    notification = await uow.notifications.get(tenant, nid)
    assert notification.status is NotificationStatus.PROCESSING
    assert len(await uow.delivery_attempts.list_for_notification(tenant, nid)) == 3


async def test_a_scheduled_notification_waits_when_sentinel_is_down_instead_of_failing():
    from datetime import datetime, timedelta, timezone

    from domain.entities.notification import Notification
    from domain.entities.workflow import Workflow
    from domain.enums import NotificationStatus
    from domain.value_objects.ids import EventId, NotificationId, TenantId, WorkflowId
    from services.ports.clients import SentinelUnavailable
    from services.notification_service import NotificationService
    from services.rendering_service import RenderingService
    from services.scheduling_service import SchedulingService
    from workflows.registry import WorkflowRegistry

    class Flaky:
        down = True

        async def resolve_recipients(self, *a):
            if self.down:
                raise SentinelUnavailable("down")
            return [ResolvedRecipient(external_ref="u1", email="u1@example.com")]

    uow, tenant, flaky = InMemoryUnitOfWork(), TenantId.new(), Flaky()
    workflow = Workflow(
        id=WorkflowId.new(), tenant_id=tenant, name="w", event_type="user.signup",
        definition={"channels": [{"channel": "email", "subject": "s", "body": "b", "format": "text"}]},
    )
    uow.workflows.items[str(workflow.id)] = workflow
    due = Notification(
        id=NotificationId.new(), tenant_id=tenant, event_id=EventId.new(), workflow_id=workflow.id,
        scheduled_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    uow.notifications.items[str(due.id)] = due
    service = SchedulingService(
        uow_factory=lambda: uow,
        notifications=NotificationService(
            uow_factory=lambda: uow, sentinel=flaky, registry=WorkflowRegistry(), rendering=RenderingService()),
        sentinel=flaky,
    )

    assert await service.dispatch_due_once() == 0
    assert due.status is NotificationStatus.CREATED          # still waiting, not failed
    flaky.down = False
    assert await service.dispatch_due_once() == 1             # picked up on the next pass
    assert due.status is NotificationStatus.PROCESSING
