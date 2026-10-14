"""
Distributed tracing for one message's journey: HTTP request -> outbox -> relay -> arq job -> worker -> provider call
(and the provider's receipt webhook).

The trace context travels as a W3C `traceparent` string stored on the delivery attempt and carried in outbox
payloads and queue jobs, so the trace survives the process hops. Until `configure_tracing` installs a provider every
helper is a cheap no-op (`traceparent()` returns None, spans do nothing), so tests and local runs pay nothing.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any, Iterator

from opentelemetry.context import Context
from opentelemetry.trace import SpanKind, Tracer
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from config.observability import ObservabilitySettings

logger = logging.getLogger(__name__)

_propagator = TraceContextTextMapPropagator()
_tracer: Tracer | None = None
_provider: Any = None


def configure_tracing(settings: ObservabilitySettings, *, service_name: str, version: str = "0.1.0") -> Any:
    """Install the OTLP span exporter for this process. No-op when disabled."""
    if not settings.enabled:
        return None
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    exporter = OTLPSpanExporter(
        endpoint=settings.endpoint.rstrip("/") + "/v1/traces",
        headers=settings.parsed_headers(),
        timeout=settings.export_timeout_seconds,
    )
    provider = install(BatchSpanProcessor(exporter), service_name=service_name, version=version,
                       environment=settings.environment, sample_ratio=settings.trace_sample_ratio)
    logger.info("Trace export enabled -> %s (service=%s)", settings.endpoint, service_name)
    return provider


def install(processor: Any, *, service_name: str = "pulse", version: str = "0.1.0",
            environment: str = "development", sample_ratio: float = 1.0) -> Any:
    """Use `processor` for this process's spans (tests pass an in-memory one). Does not touch the global provider."""
    global _tracer, _provider
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased

    provider = TracerProvider(
        resource=Resource.create({
            "service.name": service_name, "service.namespace": "pulse",
            "service.version": version, "deployment.environment": environment,
        }),
        sampler=ParentBased(TraceIdRatioBased(sample_ratio)),
    )
    provider.add_span_processor(processor)
    _provider, _tracer = provider, provider.get_tracer("pulse")
    return provider


def reset_tracing() -> None:
    """Back to no-op spans (tests)."""
    global _tracer, _provider
    _tracer = _provider = None


def shutdown_tracing() -> None:
    if _provider is not None:
        _provider.shutdown()


def traceparent() -> str | None:
    """The current span as a `traceparent` string, or None when tracing is off / there is no active span."""
    carrier: dict[str, str] = {}
    _propagator.inject(carrier)
    return carrier.get("traceparent")


def _context(parent: str | None) -> Context | None:
    if not parent:
        return None
    return _propagator.extract({"traceparent": parent})


@contextmanager
def span(
    name: str,
    *,
    parent: str | None = None,
    kind: SpanKind = SpanKind.INTERNAL,
    attributes: dict[str, Any] | None = None,
) -> Iterator[Any]:
    """
    Run a block inside a span. `parent` is a `traceparent` carried over a process boundary; without it the span
    nests under whatever is current. Exceptions are recorded on the span and re-raised.
    """
    if _tracer is None:
        yield None
        return
    with _tracer.start_as_current_span(
        name, context=_context(parent), kind=kind,
        attributes={k: v for k, v in (attributes or {}).items() if v is not None},
    ) as current:
        yield current
