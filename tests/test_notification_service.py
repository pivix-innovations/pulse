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
