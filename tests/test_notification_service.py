from __future__ import annotations

import pytest

from domain.entities.preference import Preference
from domain.enums import ChannelType, DeliveryStatus, NotificationStatus, OutboxStatus
from domain.value_objects.ids import PreferenceId, TenantId
from services.notification_service import (
    IncomingEvent,
    NoRecipientsResolved,
    NotificationService,
    UnknownEventType,
)
from services.ports.clients import ResolvedRecipient
from services.rendering_service import RenderingService
from tests.fakes import InMemoryUnitOfWork, StaticSentinelClient
from workflows.registry import WorkflowRegistry


def make_service(uow: InMemoryUnitOfWork, recipients: list[ResolvedRecipient]):
    return NotificationService(
        uow_factory=lambda: uow,
        sentinel=StaticSentinelClient(recipients),
        registry=WorkflowRegistry(),
        rendering=RenderingService(),
    )


def signup_event(key: str | None = None) -> IncomingEvent:
    return IncomingEvent(
        event_type="user.signup",
        source="quotient",
        payload={"plan": "pro"},
        idempotency_key=key,
    )


async def test_process_event_persists_notification_attempts_and_outbox_atomically():
    uow = InMemoryUnitOfWork()
    tenant = TenantId.new()
    service = make_service(uow, [ResolvedRecipient(external_ref="u1", email="u1@example.com")])

    notification_id = await service.process_event(tenant, signup_event())

    notification = await uow.notifications.get(tenant, notification_id)
    assert notification is not None
    assert notification.status is NotificationStatus.PROCESSING

    attempts = await uow.delivery_attempts.list_for_notification(tenant, notification_id)
    assert {a.channel for a in attempts} == {
        ChannelType.EMAIL,
        ChannelType.IN_APP,
        ChannelType.PUSH,
    }
    assert all(a.status is DeliveryStatus.PENDING for a in attempts)
    email = next(a for a in attempts if a.channel is ChannelType.EMAIL)
    assert "u1" in email.subject  # jinja rendered
    assert "<h1>" in email.body_html  # markdown rendered
    assert email.idempotency_key == f"{notification_id}:{email.recipient_id}:email"

    outbox = list(uow.outbox.items.values())
    assert len(outbox) == len(attempts)  # one job per attempt, same transaction
    assert all(e.status is OutboxStatus.PENDING for e in outbox)
    assert {e.topic for e in outbox} == {"deliver.email", "deliver.in_app", "deliver.push"}
    assert uow.commits == 1


async def test_unknown_event_type_rejected():
    service = make_service(InMemoryUnitOfWork(), [ResolvedRecipient(external_ref="u1")])
    with pytest.raises(UnknownEventType):
        await service.process_event(
            TenantId.new(), IncomingEvent(event_type="nope", source="quotient")
        )


async def test_no_recipients_rejected():
    service = make_service(InMemoryUnitOfWork(), [])
    with pytest.raises(NoRecipientsResolved):
        await service.process_event(TenantId.new(), signup_event())


async def test_idempotent_ingress_returns_same_notification():
    uow = InMemoryUnitOfWork()
    tenant = TenantId.new()
    service = make_service(uow, [ResolvedRecipient(external_ref="u1", email="u1@example.com")])

    first = await service.process_event(tenant, signup_event(key="evt-1"))
    second = await service.process_event(tenant, signup_event(key="evt-1"))

    assert first == second
    assert len(uow.notifications.items) == 1
    assert len(uow.outbox.items) == 3  # no second fan-out


async def test_opted_out_channel_is_skipped():
    uow = InMemoryUnitOfWork()
    tenant = TenantId.new()
    service = make_service(uow, [ResolvedRecipient(external_ref="u1", email="u1@example.com")])

    # First ingest creates the recipient row; find it and opt out of email.
    first = await service.process_event(tenant, signup_event())
    recipient = next(iter(uow.recipients.items.values()))
    await uow.preferences.upsert(
        Preference(
            id=PreferenceId.new(),
            tenant_id=tenant,
            recipient_id=recipient.id,
            channel=ChannelType.EMAIL,
            opted_in=False,
        )
    )

    second = await service.process_event(tenant, signup_event())
    assert second != first
    channels = {
        a.channel for a in await uow.delivery_attempts.list_for_notification(tenant, second)
    }
    assert ChannelType.EMAIL not in channels  # opted out
    assert channels == {ChannelType.IN_APP, ChannelType.PUSH}


async def test_quiet_hours_defer_attempt_and_outbox_publish():
    from datetime import datetime, timedelta, timezone

    from domain.value_objects.quiet_hours import QuietHours

    uow = InMemoryUnitOfWork()
    tenant = TenantId.new()
    service = make_service(uow, [ResolvedRecipient(external_ref="u1", email="u1@example.com")])

    # First pass creates the recipient row; then set quiet hours covering "now".
    await service.process_event(tenant, signup_event("k1"))
    recipient = next(iter(uow.recipients.items.values()))
    now = datetime.now(timezone.utc)
    window = QuietHours(
        start=(now - timedelta(hours=1)).time(), end=(now + timedelta(hours=1)).time()
    )
    await uow.preferences.upsert(
        Preference(
            id=PreferenceId.new(),
            tenant_id=tenant,
            recipient_id=recipient.id,
            channel=ChannelType.EMAIL,
            quiet_hours=window,
        )
    )

    nid = await service.process_event(tenant, signup_event("k2"))

    attempts = await uow.delivery_attempts.list_for_notification(tenant, nid)
    email = next(a for a in attempts if a.channel is ChannelType.EMAIL)
    push = next(a for a in attempts if a.channel is ChannelType.PUSH)
    assert email.next_retry_at is not None and email.next_retry_at > now
    assert push.next_retry_at is None  # quiet hours are per channel
    entry = next(e for e in uow.outbox.items.values() if e.aggregate_id == str(email.id))
    assert "defer_until" in entry.payload


def test_quiet_hours_window_end_wraps_midnight_and_respects_timezone():
    from datetime import datetime, time, timezone

    from domain.value_objects.quiet_hours import QuietHours

    qh = QuietHours(start=time(22, 0), end=time(7, 0), timezone="America/New_York")
    # 03:00 UTC = 23:00 New York (EDT, UTC-4) on Jun 1 -> window ends 07:00 NY = 11:00 UTC.
    end = qh.window_end(datetime(2026, 6, 1, 3, 0, tzinfo=timezone.utc))
    assert end == datetime(2026, 6, 1, 11, 0, tzinfo=timezone.utc)
    # 18:00 UTC = 14:00 NY -> outside the window.
    assert qh.window_end(datetime(2026, 6, 1, 18, 0, tzinfo=timezone.utc)) is None


async def test_database_recipient_client_falls_back_to_stored_recipient():
    from clients.sentinel_client import DatabaseRecipientClient
    from domain.entities.recipient import Recipient
    from domain.value_objects.ids import RecipientId

    uow = InMemoryUnitOfWork()
    tenant = TenantId.new()
    rid = RecipientId.new()
    uow.recipients.items[str(rid)] = Recipient(
        id=rid, tenant_id=tenant, external_ref="u9", email="u9@example.com", timezone="Asia/Kolkata"
    )
    client = DatabaseRecipientClient(lambda: uow)

    by_ref = await client.resolve_recipients(tenant, "user.signup", {"user_id": "u9"})
    assert [(r.external_ref, r.email, r.timezone) for r in by_ref] == [
        ("u9", "u9@example.com", "Asia/Kolkata")
    ]
    assert await client.resolve_recipients(tenant, "user.signup", {"user_id": "nobody"}) == []
    assert await client.resolve_recipients(tenant, "user.signup", {}) == []
    inline = await client.resolve_recipients(
        tenant, "user.signup", {"recipients": [{"external_ref": "x"}]}
    )
    assert inline[0].external_ref == "x"


async def test_a_resent_event_is_answered_without_calling_sentinel_again():
    """Idempotency is checked first: a retry works even while the recipient directory is down."""
    from services.ports.clients import SentinelUnavailable

    uow = InMemoryUnitOfWork()
    tenant = TenantId.new()
    healthy = NotificationService(
        uow_factory=lambda: uow, sentinel=StaticSentinelClient([ResolvedRecipient(external_ref="u1", email="u1@example.com")]),
        registry=WorkflowRegistry(), rendering=RenderingService(),
    )
    event = IncomingEvent(event_type="user.signup", source="t", payload={}, idempotency_key="same")
    first = await healthy.process_event(tenant, event)

    class Down:
        async def resolve_recipients(self, *a):
            raise SentinelUnavailable("down")

    outage = NotificationService(
        uow_factory=lambda: uow, sentinel=Down(), registry=WorkflowRegistry(), rendering=RenderingService(),
    )
    assert await outage.process_event(tenant, event) == first            # no Sentinel call needed
    with pytest.raises(SentinelUnavailable):                              # a *new* event does need it
        await outage.process_event(
            tenant, IncomingEvent(event_type="user.signup", source="t", payload={}, idempotency_key="new"))
