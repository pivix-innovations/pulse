from __future__ import annotations

import asyncio

import pytest
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

import telemetry.metrics as tm
from config.observability import ObservabilitySettings
from domain.entities.operations import OperationsSnapshot
from domain.enums import ChannelType, DeliveryStatus
from telemetry.metrics import OperationsCollector, PulseMetrics, configure_metrics, get_metrics


@pytest.fixture
def reader():
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    tm._metrics = PulseMetrics(provider.get_meter("test"))
    yield reader, provider
    tm.reset_metrics()


def points(reader, name):
    data = reader.get_metrics_data()
    out = []
    for rm in data.resource_metrics if data else []:
        for sm in rm.scope_metrics:
            for m in sm.metrics:
                if m.name == name:
                    out.extend(m.data.data_points)
    return out


def test_disabled_telemetry_is_a_noop_and_recording_is_safe():
    tm.reset_metrics()
    assert configure_metrics(ObservabilitySettings(enabled=False), service_name="x") is None
    get_metrics().events_ingested.add(1, {"tenant": "t"})  # must not raise


def test_headers_parse_into_a_dict():
    s = ObservabilitySettings(headers="Authorization=Basic abc==, X-Org = acme ,bogus")
    assert s.parsed_headers() == {"Authorization": "Basic abc==", "X-Org": "acme"}


def test_counters_and_histograms_carry_their_attributes(reader):
    r, _ = reader
    m = get_metrics()
    m.deliveries.add(1, {"channel": "email", "outcome": "sent", "provider": "smtp"})
    m.deliveries.add(1, {"channel": "email", "outcome": "sent", "provider": "smtp"})
    m.provider_duration.record(120, {"provider": "smtp", "channel": "email", "outcome": "ok"})
    [p] = points(r, "pulse.deliveries")
    assert p.value == 2 and dict(p.attributes)["outcome"] == "sent"
    [h] = points(r, "pulse.provider.duration")
    assert h.count == 1 and h.sum == 120


async def test_collector_publishes_pipeline_state_as_gauges(reader):
    r, provider = reader
    snap = OperationsSnapshot(
        outbox_pending=7,
        outbox_oldest_age_seconds=42.5,
        open_attempts={(ChannelType.EMAIL, DeliveryStatus.RETRYING): 3},
        stuck_attempts=2,
        dead_letters=5,
    )

    async def snapshot():
        return snap

    async def depths():
        return {"pulse:email": 11, "pulse:sms": 0}

    collector = OperationsCollector(
        provider.get_meter("test"), snapshot=snapshot, queue_depths=depths, refresh_seconds=60
    )
    await collector.refresh_once()

    assert points(r, "pulse.outbox.pending")[0].value == 7
    assert points(r, "pulse.outbox.oldest_age")[0].value == 42.5
    assert points(r, "pulse.attempts.stuck")[0].value == 2
    assert points(r, "pulse.dead_letters.total")[0].value == 5
    [open_pt] = points(r, "pulse.attempts.open")
    assert open_pt.value == 3 and dict(open_pt.attributes) == {"channel": "email", "status": "retrying"}
    depth = {dict(p.attributes)["queue"]: p.value for p in points(r, "pulse.queue.depth")}
    assert depth == {"pulse:email": 11, "pulse:sms": 0}


async def test_collector_survives_a_failing_snapshot(reader):
    _, provider = reader
    calls = 0

    async def snapshot():
        nonlocal calls
        calls += 1
        raise RuntimeError("db down")

    async def depths():
        return {}

    c = OperationsCollector(provider.get_meter("t2"), snapshot=snapshot, queue_depths=depths, refresh_seconds=0.01)
    stop = asyncio.Event()
    task = asyncio.create_task(c.run(stop))
    await asyncio.sleep(0.08)
    stop.set()
    await task
    assert calls >= 2  # kept trying after the first failure
