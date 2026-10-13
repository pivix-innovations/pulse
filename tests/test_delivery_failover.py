from __future__ import annotations

import asyncio

from config.retry import RetrySettings
from domain.entities.device import DeviceToken
from domain.enums import ChannelType, DeliveryStatus, DevicePlatform
from domain.value_objects.ids import DeviceTokenId, TenantId
from services.ports.providers import ProviderSendError, SendResult
from tests.fakes import FakeProvider, InMemoryUnitOfWork
from tests.test_delivery_service import seed_attempt
from services.delivery_service import DeliveryService
from tests.fakes import FakeDispatchGuard, FakeProviderRegistry, FakePublisher


def chain_service(uow, providers, retry=None):
    return DeliveryService(
        uow_factory=lambda: uow,
        providers=FakeProviderRegistry(providers),
        guard=FakeDispatchGuard(),
        publisher=FakePublisher(),
        retry=retry or RetrySettings(),
    )


async def deliver(providers, channel=ChannelType.EMAIL, retry=None):
    uow = InMemoryUnitOfWork()
    tenant = TenantId.new()
    attempt = seed_attempt(uow, tenant, channel)
    result = await chain_service(uow, providers, retry).deliver(tenant, attempt.id)
    return result, uow.delivery_attempts.items[str(attempt.id)], uow, tenant


async def test_hard_error_fails_over_to_next_provider():
    first, second = FakeProvider(fail=True), FakeProvider()
    result, attempt, *_ = await deliver([first, second])
    assert result == "sent"
    assert len(second.sent) == 1 and not first.sent
    assert attempt.status is DeliveryStatus.SENT
    # Attributed to the provider that actually sent it.
    assert attempt.provider_id is not None


async def test_timeout_fails_over():
    class Slow(FakeProvider):
        async def send(self, message):
            await asyncio.sleep(1)

    second = FakeProvider()
    result, *_ = await deliver([Slow(), second], retry=RetrySettings(provider_timeout_seconds=0.05))
    assert result == "sent" and len(second.sent) == 1


async def test_success_never_touches_later_providers():
    first, second = FakeProvider(confirms_delivery=False), FakeProvider()
    result, attempt, *_ = await deliver([first, second])
    assert result == "sent"
    assert len(first.sent) == 1 and not second.sent
    # Accepted-but-unconfirmed is final for the chain: no failover.
    assert attempt.expects_confirmation is False


async def test_failover_false_stops_the_chain():
    first = FakeProvider(error=ProviderSendError("bad address", retryable=False, failover=False))
    second = FakeProvider()
    result, attempt, *_ = await deliver([first, second])
    assert result == "failed"
    assert not second.sent
    assert attempt.status is DeliveryStatus.FAILED


async def test_every_provider_failing_reports_all_errors_and_retries():
    result, attempt, *_ = await deliver([FakeProvider(fail=True), FakeProvider(fail=True)])
    assert result == "retrying"
    assert attempt.last_error.count("provider exploded") == 2
    assert "p0:" in attempt.last_error and "p1:" in attempt.last_error


async def test_chain_exhausted_with_only_permanent_failures_is_not_retried():
    perm = ProviderSendError("credentials rejected", retryable=False)
    result, attempt, *_ = await deliver([FakeProvider(error=perm), FakeProvider(error=perm)])
    assert result == "failed"
    assert attempt.status is DeliveryStatus.FAILED  # left for channel fallback


async def test_dead_push_tokens_are_pruned_even_when_delivery_fails():
    uow = InMemoryUnitOfWork()
    tenant = TenantId.new()
    attempt = seed_attempt(uow, tenant, ChannelType.PUSH)
    for tok in ("dead-1", "live-1"):
        uow.device_tokens.items[tok] = DeviceToken(
            id=DeviceTokenId.new(),
            tenant_id=tenant,
            recipient_id=attempt.recipient_id,
            platform=DevicePlatform.ANDROID,
            token=tok,
        )
    provider = FakeProvider(
        channel=ChannelType.PUSH,
        error=ProviderSendError(
            "all invalid", retryable=False, failover=False, invalid_destinations=("dead-1",)
        ),
    )
    await chain_service(uow, [provider]).deliver(tenant, attempt.id)
    assert [t.token for t in uow.device_tokens.items.values()] == ["live-1"]


async def test_stale_tokens_reported_on_success_are_pruned_too():
    class Partial(FakeProvider):
        async def send(self, message):
            return SendResult(provider_message_id="x", invalid_destinations=("dead-1",))

    uow = InMemoryUnitOfWork()
    tenant = TenantId.new()
    attempt = seed_attempt(uow, tenant, ChannelType.PUSH)
    uow.device_tokens.items["a"] = DeviceToken(
        id=DeviceTokenId.new(), tenant_id=tenant, recipient_id=attempt.recipient_id,
        platform=DevicePlatform.IOS, token="dead-1",
    )
    uow.device_tokens.items["b"] = DeviceToken(
        id=DeviceTokenId.new(), tenant_id=tenant, recipient_id=attempt.recipient_id,
        platform=DevicePlatform.IOS, token="live-1",
    )
    assert await chain_service(uow, [Partial(channel=ChannelType.PUSH)]).deliver(tenant, attempt.id) == "sent"
    assert [t.token for t in uow.device_tokens.items.values()] == ["live-1"]
