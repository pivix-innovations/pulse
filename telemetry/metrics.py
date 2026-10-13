"""
Pulse's operational metrics (OpenTelemetry).

Call sites use `get_metrics()` and record unconditionally: until
`configure_metrics` installs a provider every instrument is a no-op, so
tests and local runs pay nothing.

Event counters/histograms are recorded by whichever process does the work
(API, workers). Pipeline *state* (outbox lag, queue depth, stuck attempts)
is exported as gauges by the `OperationsCollector`, which runs only in the
scheduler process so each series has exactly one writer.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable

from opentelemetry.metrics import CallbackOptions, Meter, Observation

from config.observability import ObservabilitySettings
from domain.entities.operations import OperationsSnapshot

logger = logging.getLogger(__name__)

_LATENCY_BUCKETS_MS = [5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000, 30000]


class PulseMetrics:
    def __init__(self, meter: Meter) -> None:
        self.events_ingested = meter.create_counter(
            "pulse.events.ingested", unit="{event}", description="Events accepted by POST /events"
        )
        self.ratelimit_rejected = meter.create_counter(
            "pulse.ratelimit.rejected", unit="{request}", description="Requests rejected with 429"
        )
        self.deliveries = meter.create_counter(
            "pulse.deliveries",
            unit="{attempt}",
            description="Delivery attempt outcomes (sent, retrying, dead, failed, ...)",
        )
        self.dead_letters = meter.create_counter(
            "pulse.dead_letters", unit="{attempt}", description="Attempts moved to the dead-letter table"
        )
        self.provider_calls = meter.create_counter(
            "pulse.provider.calls", unit="{call}", description="Provider send calls by outcome"
        )
        self.provider_duration = meter.create_histogram(
            "pulse.provider.duration",
            unit="ms",
            description="Provider send latency",
            explicit_bucket_boundaries_advisory=_LATENCY_BUCKETS_MS,
        )
        self.http_duration = meter.create_histogram(
            "pulse.http.server.duration",
            unit="ms",
            description="API request latency by route and status class",
            explicit_bucket_boundaries_advisory=_LATENCY_BUCKETS_MS,
        )


_noop_meter_name = "pulse"
_metrics: PulseMetrics | None = None


def get_metrics() -> PulseMetrics:
    global _metrics
    if _metrics is None:
        from opentelemetry import metrics as otel_metrics

        _metrics = PulseMetrics(otel_metrics.get_meter(_noop_meter_name))
    return _metrics


class Telemetry:
    """Owns the MeterProvider so a process can flush it on shutdown."""

    def __init__(self, provider: Any, meter: Meter) -> None:
        self.provider = provider
        self.meter = meter

    def shutdown(self) -> None:
        try:
            self.provider.shutdown()
        except Exception:
            logger.exception("Telemetry shutdown failed")


def configure_metrics(
    settings: ObservabilitySettings, *, service_name: str, version: str = "0.1.0"
) -> Telemetry | None:
    """Install the OTLP exporter for this process. No-op when disabled."""
    global _metrics
    if not settings.enabled:
        return None
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.resources import Resource

    exporter = OTLPMetricExporter(
        endpoint=settings.endpoint.rstrip("/") + "/v1/metrics",
        headers=settings.parsed_headers(),
        timeout=settings.export_timeout_seconds,
    )
    reader = PeriodicExportingMetricReader(
        exporter, export_interval_millis=int(settings.export_interval_seconds * 1000)
    )
    provider = MeterProvider(
        metric_readers=[reader],
        resource=Resource.create(
            {
                "service.name": service_name,
                "service.namespace": "pulse",
                "service.version": version,
                "deployment.environment": settings.environment,
            }
        ),
    )
    meter = provider.get_meter("pulse")
    _metrics = PulseMetrics(meter)
    logger.info("Metrics export enabled -> %s (service=%s)", settings.endpoint, service_name)
    return Telemetry(provider, meter)


def reset_metrics() -> None:
    """Back to no-op instruments (tests)."""
    global _metrics
    _metrics = None


class OperationsCollector:
    """
    Periodically samples pipeline state and serves it to observable gauges.

    Gauges read a cached snapshot (callbacks must be synchronous); a
    background loop refreshes it, so a slow database never blocks export.
    """

    def __init__(
        self,
        meter: Meter,
        *,
        snapshot: Callable[[], Any],
        queue_depths: Callable[[], Any],
        refresh_seconds: float,
    ) -> None:
        self._snapshot_fn = snapshot
        self._queues_fn = queue_depths
        self._refresh = refresh_seconds
        self._snap = OperationsSnapshot()
        self._queues: dict[str, int] = {}
        meter.create_observable_gauge(
            "pulse.outbox.pending", callbacks=[self._outbox_pending], unit="{entry}",
            description="Outbox entries waiting for the relay",
        )
        meter.create_observable_gauge(
            "pulse.outbox.oldest_age", callbacks=[self._outbox_age], unit="s",
            description="Age of the oldest unpublished outbox entry (relay lag)",
        )
        meter.create_observable_gauge(
            "pulse.attempts.open", callbacks=[self._open], unit="{attempt}",
            description="Unsettled delivery attempts by channel and status",
        )
        meter.create_observable_gauge(
            "pulse.attempts.stuck", callbacks=[self._stuck], unit="{attempt}",
            description="Pending/retrying attempts nobody is working",
        )
        meter.create_observable_gauge(
            "pulse.dead_letters.total", callbacks=[self._dead], unit="{attempt}",
            description="Rows in the dead-letter table",
        )
        meter.create_observable_gauge(
            "pulse.queue.depth", callbacks=[self._depth], unit="{job}",
            description="Jobs waiting in each worker queue",
        )

    def _outbox_pending(self, _: CallbackOptions) -> Iterable[Observation]:
        yield Observation(self._snap.outbox_pending)

    def _outbox_age(self, _: CallbackOptions) -> Iterable[Observation]:
        yield Observation(self._snap.outbox_oldest_age_seconds)

    def _open(self, _: CallbackOptions) -> Iterable[Observation]:
        for (channel, status), n in self._snap.open_attempts.items():
            yield Observation(n, {"channel": channel.value, "status": status.value})

    def _stuck(self, _: CallbackOptions) -> Iterable[Observation]:
        yield Observation(self._snap.stuck_attempts)

    def _dead(self, _: CallbackOptions) -> Iterable[Observation]:
        yield Observation(self._snap.dead_letters)

    def _depth(self, _: CallbackOptions) -> Iterable[Observation]:
        for queue, depth in self._queues.items():
            yield Observation(depth, {"queue": queue})

    async def refresh_once(self) -> None:
        self._snap = await self._snapshot_fn()
        self._queues = await self._queues_fn()

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await self.refresh_once()
            except Exception:
                logger.exception("Operations snapshot failed")
            try:
                await asyncio.wait_for(stop.wait(), timeout=self._refresh)
            except asyncio.TimeoutError:
                pass


def build_snapshot_fn(uow_factory: Callable[[], Any], reconciliation: Any):
    async def snapshot() -> OperationsSnapshot:
        now = datetime.now(timezone.utc)
        async with uow_factory() as uow:
            return await uow.operations.snapshot(
                now,
                now - timedelta(seconds=reconciliation.stuck_lost_ttl_seconds),
                now - timedelta(seconds=reconciliation.stuck_in_doubt_ttl_seconds),
            )

    return snapshot


def build_queue_depth_fn(redis: Any, queues: Iterable[str]):
    names = list(queues)

    async def depths() -> dict[str, int]:
        # arq keeps each queue as a sorted set keyed by the queue name.
        return {q: int(await redis.zcard(q)) for q in names}

    return depths
