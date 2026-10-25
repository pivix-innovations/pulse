"""One message, one trace: request -> attempt/outbox -> relay -> worker -> provider call -> receipt webhook."""
from __future__ import annotations

import pytest
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from config.outbox import OutboxSettings
from config.retry import RetrySettings
from domain.entities.outbox import OutboxEntry
from domain.value_objects.ids import OutboxEntryId, TenantId
from jobqueue.relay import OutboxRelay
from services.delivery_service import DeliveryService
from telemetry import tracing
from tests.fakes import FakeDispatchGuard, FakeProvider, FakeProviderRegistry, FakePublisher, InMemoryUnitOfWork
from tests.test_delivery_service import seed_attempt

TRACE_ID = "0af7651916cd43dd8448eb211c80319c"
INCOMING = f"00-{TRACE_ID}-b7ad6b7169203331-01"


@pytest.fixture
def spans():
    exporter = InMemorySpanExporter()
    tracing.install(SimpleSpanProcessor(exporter))
    yield exporter
    tracing.reset_tracing()


def trace_id_of(traceparent: str) -> str:
    return traceparent.split("-")[1]


def test_helpers_are_no_ops_until_installed():
    tracing.reset_tracing()
    with tracing.span("x") as s:
        assert s is None and tracing.traceparent() is None


async def test_relay_hands_the_worker_its_own_span_in_the_same_trace(spans):
    uow = InMemoryUnitOfWork()
    entry = OutboxEntry(
        id=OutboxEntryId.new(), tenant_id=TenantId.new(), aggregate_type="delivery_attempt", aggregate_id="a",
        topic="deliver.email", payload={"attempt_id": "a", "traceparent": INCOMING},
    )
    uow.outbox.items[str(entry.id)] = entry
    publisher = FakePublisher()
    await OutboxRelay(lambda: uow, publisher, OutboxSettings()).relay_once()

    sent = publisher.published[0][1]["traceparent"]
    assert trace_id_of(sent) == TRACE_ID and sent != INCOMING
    [relay_span] = [s for s in spans.get_finished_spans() if s.name == "outbox.publish"]
    assert relay_span.parent.span_id == int("b7ad6b7169203331", 16)
    assert f"{relay_span.context.span_id:016x}" == sent.split("-")[2]


async def test_delivery_span_continues_the_job_trace_and_wraps_the_provider_call(spans):
    uow, tenant = InMemoryUnitOfWork(), TenantId.new()
    attempt = seed_attempt(uow, tenant)
    service = DeliveryService(
        uow_factory=lambda: uow, providers=FakeProviderRegistry(FakeProvider()),
        guard=FakeDispatchGuard(), publisher=FakePublisher(), retry=RetrySettings(),
    )
    assert await service.deliver(tenant, attempt.id, traceparent=INCOMING) == "sent"

    by_name = {s.name: s for s in spans.get_finished_spans()}
    assert by_name["delivery.attempt"].context.trace_id == int(TRACE_ID, 16)
    assert by_name["delivery.attempt"].attributes["pulse.outcome"] == "sent"
    assert by_name["provider.send"].parent.span_id == by_name["delivery.attempt"].context.span_id


async def test_a_failing_provider_marks_the_span_as_an_error_and_retry_keeps_the_trace(spans):
    uow, tenant = InMemoryUnitOfWork(), TenantId.new()
    attempt = seed_attempt(uow, tenant)
    publisher = FakePublisher()
    service = DeliveryService(
        uow_factory=lambda: uow, providers=FakeProviderRegistry(FakeProvider(fail=True)),
        guard=FakeDispatchGuard(), publisher=publisher, retry=RetrySettings(),
    )
    await service.deliver(tenant, attempt.id, traceparent=INCOMING)
    retry_payload = publisher.published[0][1]
    assert trace_id_of(retry_payload["traceparent"]) == TRACE_ID
