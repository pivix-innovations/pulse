from __future__ import annotations

import pytest
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from config.api import ApiSettings
from config.loader import get_settings
from domain.enums import ChannelType
from services.secrets import SecretBox, generate_key
from telemetry import tracing
from tests.api.test_events_notifications import EVENT
from tests.api.test_provider_webhooks import PUBLIC, TWILIO, make_provider, seed_sent_attempt, twilio_call

TRACE_ID = "0af7651916cd43dd8448eb211c80319c"
INCOMING = f"00-{TRACE_ID}-b7ad6b7169203331-01"


def trace_id_of(traceparent: str) -> str:
    return traceparent.split("-")[1]


@pytest.fixture
def traced_world(world):
    world.app.state.secret_box = SecretBox(generate_key())
    world.app.state.settings = get_settings().model_copy(update={"api": ApiSettings(public_url=PUBLIC)})
    exporter = InMemorySpanExporter()
    tracing.install(SimpleSpanProcessor(exporter))
    yield world, exporter
    tracing.reset_tracing()


async def test_receipt_webhook_span_joins_the_trace_stored_on_the_attempt(traced_world):
    world, exporter = traced_world
    provider = await make_provider(world, TWILIO)
    attempt, _ = seed_sent_attempt(world, provider["id"], ChannelType.SMS, "SM-T1")
    attempt.traceparent = INCOMING

    r = await twilio_call(world, provider, {"MessageSid": "SM-T1", "MessageStatus": "delivered"})
    assert r.status_code == 200 and r.json() == {"processed": 1}
    [receipt] = [s for s in exporter.get_finished_spans() if s.name == "webhook.receipt"]
    assert receipt.context.trace_id == int(TRACE_ID, 16)
    assert receipt.parent.span_id == int("b7ad6b7169203331", 16)


async def test_the_request_trace_flows_into_the_attempt_and_outbox(traced_world):
    world, exporter = traced_world
    r = await world.client.post(
        "/api/v1/events", json={**EVENT, "idempotency_key": "t1"}, headers={**world.key_headers, "traceparent": INCOMING}
    )
    assert r.status_code == 202
    attempts = list(world.uow.delivery_attempts.items.values())
    assert attempts and all(trace_id_of(a.traceparent) == TRACE_ID for a in attempts)
    assert all(trace_id_of(e.payload["traceparent"]) == TRACE_ID for e in world.uow.outbox.items.values())
    [server] = [s for s in exporter.get_finished_spans() if s.name == "POST /events"]
    assert server.parent.span_id == int("b7ad6b7169203331", 16)        # continued the caller's trace
    assert server.attributes["http.response.status_code"] == 202
