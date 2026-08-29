from __future__ import annotations

from datetime import datetime, timezone

from config.retry import RetrySettings
from domain.entities.delivery import DeliveryAttempt
from domain.entities.device import DeviceToken
from domain.entities.recipient import Recipient
from domain.enums import ChannelType, DeliveryStatus, DevicePlatform
from domain.value_objects.ids import (
    DeliveryAttemptId,
    DeviceTokenId,
    NotificationId,
    RecipientId,
    TenantId,
)
from services.delivery_service import DeliveryService
from services.ports.providers import ProviderSendError
from tests.fakes import (
    FakeDispatchGuard,
    FakePublisher,
    FakeProvider,
    FakeProviderRegistry,
    InMemoryUnitOfWork,
)


def seed_attempt(
    uow: InMemoryUnitOfWork, tenant: TenantId, channel: ChannelType = ChannelType.EMAIL
) -> DeliveryAttempt:
    recipient = Recipient(
        id=RecipientId.new(),
        tenant_id=tenant,
        external_ref="u1",
        email="u1@example.com",
        phone="+15550001111",
    )
    uow.recipients.items[str(recipient.id)] = recipient
    attempt = DeliveryAttempt(
        id=DeliveryAttemptId.new(),
        tenant_id=tenant,
        notification_id=NotificationId.new(),
        recipient_id=recipient.id,
        channel=channel,
        subject="hi",
        body="hello",
        body_html="<p>hello</p>",
    )
    uow.delivery_attempts.items[str(attempt.id)] = attempt
    return attempt


def make_service(
    uow,
    provider: FakeProvider,
    guard: FakeDispatchGuard | None = None,
    publisher: FakePublisher | None = None,
    retry: RetrySettings | None = None,
):
    return DeliveryService(
        uow_factory=lambda: uow,
        providers=FakeProviderRegistry(provider),
        guard=guard or FakeDispatchGuard(),
        publisher=publisher or FakePublisher(),
        retry=retry or RetrySettings(),
    )


async def test_successful_dispatch_marks_sent():
    uow = InMemoryUnitOfWork()
    tenant = TenantId.new()
    attempt = seed_attempt(uow, tenant)
    provider = FakeProvider()

    result = await make_service(uow, provider).deliver(tenant, attempt.id)

    assert result == "sent"
    assert attempt.status is DeliveryStatus.SENT
    assert attempt.delivered_at is None  # sent != delivered
    assert attempt.attempt_count == 1
    assert provider.sent[0].to == "u1@example.com"
    assert provider.sent[0].idempotency_key == attempt.idempotency_key


async def test_duplicate_dispatch_blocked_by_guard():
    uow = InMemoryUnitOfWork()
    tenant = TenantId.new()
    attempt = seed_attempt(uow, tenant)
    provider = FakeProvider()
    guard = FakeDispatchGuard()
    await guard.acquire(attempt.idempotency_key)  # simulate a prior dispatch

    result = await make_service(uow, provider, guard).deliver(tenant, attempt.id)

    assert result == "duplicate"
    assert provider.sent == []
    assert attempt.status is DeliveryStatus.PENDING


async def test_retryable_failure_schedules_backoff_retry():
    uow = InMemoryUnitOfWork()
    tenant = TenantId.new()
    attempt = seed_attempt(uow, tenant)
    guard = FakeDispatchGuard()
    publisher = FakePublisher()

    result = await make_service(uow, FakeProvider(fail=True), guard, publisher).deliver(
        tenant, attempt.id
    )

    assert result == "retrying"
    assert attempt.status is DeliveryStatus.RETRYING
    assert attempt.attempt_count == 1
    assert attempt.next_retry_at is not None
    assert attempt.idempotency_key not in guard.keys  # released for the retry
    topic, payload, job_id, defer = publisher.published[0]
    assert topic == "deliver.email"
    assert payload["attempt_id"] == str(attempt.id)
    assert job_id == f"retry:{attempt.id}:1"
    assert defer is not None and defer > 0


async def test_retry_exhaustion_dead_letters():
    uow = InMemoryUnitOfWork()
    tenant = TenantId.new()
    attempt = seed_attempt(uow, tenant)
    publisher = FakePublisher()
    service = make_service(
        uow, FakeProvider(fail=True), publisher=publisher, retry=RetrySettings(max_attempts=2)
    )

    assert await service.deliver(tenant, attempt.id) == "retrying"  # try 1
    assert await service.deliver(tenant, attempt.id) == "dead"  # try 2 -> exhausted

    assert attempt.status is DeliveryStatus.DEAD
    assert len(uow.dead_letters.items) == 1
    assert "Retries exhausted" in uow.dead_letters.items[0].reason
    assert len(publisher.published) == 1  # no retry scheduled after death


async def test_non_retryable_failure_stays_failed_for_sweep():
    uow = InMemoryUnitOfWork()
    tenant = TenantId.new()
    attempt = seed_attempt(uow, tenant, channel=ChannelType.IN_APP)
    publisher = FakePublisher()
    provider = FakeProvider(error=ProviderSendError("no live SSE connection", retryable=False))

    result = await make_service(uow, provider, publisher=publisher).deliver(tenant, attempt.id)

    assert result == "failed"
    assert attempt.status is DeliveryStatus.FAILED
    assert publisher.published == []  # no retry; sweep escalates instead
    assert uow.dead_letters.items == []


async def test_push_dispatch_carries_device_tokens():
    uow = InMemoryUnitOfWork()
    tenant = TenantId.new()
    attempt = seed_attempt(uow, tenant, channel=ChannelType.PUSH)
    token = DeviceToken(
        id=DeviceTokenId.new(),
        tenant_id=tenant,
        recipient_id=attempt.recipient_id,
        platform=DevicePlatform.ANDROID,
        token="fcm-token-1",
    )
    uow.device_tokens.items[str(token.id)] = token
    provider = FakeProvider(channel=ChannelType.PUSH)

    result = await make_service(uow, provider).deliver(tenant, attempt.id)

    assert result == "sent"
    assert provider.sent[0].metadata["device_tokens"] == ["fcm-token-1"]
    assert provider.sent[0].metadata["attempt_id"] == str(attempt.id)


async def test_push_without_tokens_fails_non_retryably():
    uow = InMemoryUnitOfWork()
    tenant = TenantId.new()
    attempt = seed_attempt(uow, tenant, channel=ChannelType.PUSH)
    publisher = FakePublisher()

    result = await make_service(
        uow, FakeProvider(channel=ChannelType.PUSH), publisher=publisher
    ).deliver(tenant, attempt.id)

    assert result == "failed"
    assert attempt.status is DeliveryStatus.FAILED
    assert publisher.published == []  # sweep escalates push -> sms


async def test_already_sent_attempt_is_skipped():
    uow = InMemoryUnitOfWork()
    tenant = TenantId.new()
    attempt = seed_attempt(uow, tenant)
    attempt.mark_sent(datetime.now(timezone.utc))
    provider = FakeProvider()

    result = await make_service(uow, provider).deliver(tenant, attempt.id)

    assert result == "skipped"
    assert provider.sent == []
