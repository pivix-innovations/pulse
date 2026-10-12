"""
Integration tests wiring the REAL services together (NotificationService ->
OutboxRelay -> DeliveryService -> ReconciliationService) over in-memory
fakes for infrastructure: an inline publisher plays the role of the queue +
worker, so a full pipeline pass needs no Postgres or Redis.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from config.outbox import OutboxSettings
from config.reconciliation import ReconciliationSettings
from config.retry import RetrySettings
from domain.enums import ChannelType, DeliveryStatus
from domain.value_objects.ids import TenantId
from jobqueue.relay import OutboxRelay
from services.delivery_service import DeliveryService
from services.notification_service import IncomingEvent, NotificationService
from services.ports.clients import ResolvedRecipient
from services.ports.providers import ProviderSendError
from services.reconciliation_service import ReconciliationService
from services.rendering_service import RenderingService
from tests.fakes import FakeDispatchGuard, FakePublisher, FakeProvider, InMemoryUnitOfWork
from workflows.registry import WorkflowRegistry


class ChannelProviderMap:
    """Provider registry fake with one FakeProvider per channel."""

    def __init__(self, providers: dict[ChannelType, FakeProvider]) -> None:
        self.providers = providers

    async def resolve(self, uow, tenant_id, channel: ChannelType):
        return self.providers[channel], None


class InlineWorkerPublisher:
    """Queue + worker collapsed into one: enqueue executes the delivery."""

    def __init__(self) -> None:
        self.delivery: DeliveryService | None = None
        self.jobs: list[dict] = []

    async def enqueue(self, topic, payload, *, job_id=None, defer_by_seconds=None) -> None:
        self.jobs.append(payload)
        from domain.value_objects.ids import DeliveryAttemptId

        await self.delivery.deliver(
            TenantId.from_str(payload["tenant_id"]),
            DeliveryAttemptId.from_str(payload["attempt_id"]),
        )


def build_pipeline(uow: InMemoryUnitOfWork, providers: dict[ChannelType, FakeProvider]):
    ingress = NotificationService(
        uow_factory=lambda: uow,
        sentinel=None,  # not used; recipients passed via process_event caller
        registry=WorkflowRegistry(),
        rendering=RenderingService(),
    )
    publisher = InlineWorkerPublisher()
    publisher.delivery = DeliveryService(
        uow_factory=lambda: uow,
        providers=ChannelProviderMap(providers),
        guard=FakeDispatchGuard(),
        publisher=FakePublisher(),  # app-level retries not exercised here
        retry=RetrySettings(),
    )
    relay = OutboxRelay(lambda: uow, publisher, OutboxSettings())
    sweep = ReconciliationService(lambda: uow, RenderingService(), ReconciliationSettings())
    return ingress, relay, sweep


RECIPIENT = ResolvedRecipient(
    external_ref="ada", email="ada@example.com", phone="+15550009999"
)


async def ingest(ingress: NotificationService, uow: InMemoryUnitOfWork, tenant: TenantId):
    from domain.entities.device import DeviceToken
    from domain.enums import DevicePlatform
    from domain.value_objects.ids import DeviceTokenId
    from tests.fakes import StaticSentinelClient

    ingress._sentinel = StaticSentinelClient([RECIPIENT])
    nid = await ingress.process_event(
        tenant,
        IncomingEvent(event_type="user.signup", source="quotient", payload={"plan": "pro"}),
    )
    # The recipient's app registered a push token.
    recipient = await uow.recipients.get_by_external_ref(tenant, RECIPIENT.external_ref)
    await uow.device_tokens.upsert(
        DeviceToken(
            id=DeviceTokenId.new(),
            tenant_id=tenant,
            recipient_id=recipient.id,
            platform=DevicePlatform.ANDROID,
            token="device-token-1",
        )
    )
    return nid


async def test_event_to_delivery_to_ack():
    uow = InMemoryUnitOfWork()
    tenant = TenantId.new()
    providers = {
        ChannelType.EMAIL: FakeProvider(ChannelType.EMAIL),
        ChannelType.IN_APP: FakeProvider(ChannelType.IN_APP),
        ChannelType.PUSH: FakeProvider(ChannelType.PUSH),
    }
    ingress, relay, _ = build_pipeline(uow, providers)

    nid = await ingest(ingress, uow, tenant)
    relayed = await relay.relay_once()

    assert relayed == 3
    attempts = {
        a.channel: a for a in await uow.delivery_attempts.list_for_notification(tenant, nid)
    }
    assert all(a.status is DeliveryStatus.SENT for a in attempts.values())
    assert providers[ChannelType.EMAIL].sent[0].to == "ada@example.com"
    assert "<h1>" in providers[ChannelType.EMAIL].sent[0].body_html

    # The client acks the in-app attempt: sent -> delivered.
    in_app = attempts[ChannelType.IN_APP]
    in_app.mark_delivered(datetime.now(timezone.utc))
    await uow.delivery_attempts.update(in_app)

    assert in_app.status is DeliveryStatus.DELIVERED
    assert in_app.delivered_at is not None
    # And the others remain merely `sent`: acceptance is not delivery.
    assert attempts[ChannelType.EMAIL].status is DeliveryStatus.SENT
    # Nothing left in the outbox.
    assert all(e.status.value == "published" for e in uow.outbox.items.values())


async def test_sweep_driven_fallback_escalation():
    uow = InMemoryUnitOfWork()
    tenant = TenantId.new()
    providers = {
        ChannelType.EMAIL: FakeProvider(ChannelType.EMAIL),
        # No live SSE connection: non-retryable failure.
        ChannelType.IN_APP: FakeProvider(
            ChannelType.IN_APP,
            error=ProviderSendError("No live SSE connection", retryable=False),
        ),
        ChannelType.PUSH: FakeProvider(ChannelType.PUSH),
        ChannelType.SMS: FakeProvider(ChannelType.SMS),
    }
    ingress, relay, sweep = build_pipeline(uow, providers)

    nid = await ingest(ingress, uow, tenant)
    await relay.relay_once()

    attempts = {
        a.channel: a for a in await uow.delivery_attempts.list_for_notification(tenant, nid)
    }
    assert attempts[ChannelType.PUSH].status is DeliveryStatus.SENT
    assert attempts[ChannelType.IN_APP].status is DeliveryStatus.FAILED

    # Time passes with no ack: age the push past its TTL, then sweep.
    attempts[ChannelType.PUSH].sent_at = datetime.now(timezone.utc) - timedelta(hours=1)
    handled = await sweep.sweep_once()
    assert handled == 2  # stuck push + failed in_app

    # Push escalated to SMS; in_app's fallback (push) already attempted -> capped.
    attempts = {
        a.channel: a for a in await uow.delivery_attempts.list_for_notification(tenant, nid)
    }
    assert attempts[ChannelType.PUSH].status is DeliveryStatus.DEAD
    assert attempts[ChannelType.IN_APP].status is DeliveryStatus.DEAD
    sms = attempts[ChannelType.SMS]
    assert sms.status is DeliveryStatus.PENDING
    assert "ada" in sms.body  # fallback template rendered for this recipient
    assert any("already attempted" in d.reason for d in uow.dead_letters.items)

    # The relay picks up the escalation's outbox entry and the SMS goes out.
    await relay.relay_once()
    assert sms.status is DeliveryStatus.SENT
    assert providers[ChannelType.SMS].sent[0].to == "+15550009999"

    # Provider DLR confirms delivery (webhook path's state change).
    sms.mark_delivered(datetime.now(timezone.utc))
    assert sms.status is DeliveryStatus.DELIVERED

    # A later sweep finds nothing more to do: escalation is capped.
    attempts_before = len(uow.delivery_attempts.items)
    await sweep.sweep_once()
    assert len(uow.delivery_attempts.items) == attempts_before
