from __future__ import annotations

from config.base import ConfigSection
from pydantic import PositiveFloat, PositiveInt


class ObservabilitySettings(ConfigSection):
    """
    OpenTelemetry export (metrics today, traces next) over OTLP/HTTP. Works
    with any OTLP backend; OpenObserve is the reference one:

        PULSE_OTEL__ENABLED=true
        PULSE_OTEL__ENDPOINT=http://localhost:5080/api/default
        PULSE_OTEL__HEADERS=Authorization=Basic <base64 user:password>
    """

    enabled: bool = False
    # Base URL of the OTLP/HTTP receiver; `/v1/metrics` is appended.
    endpoint: str = "http://localhost:5080/api/default"
    # Comma-separated `key=value` pairs sent with every export.
    headers: str = ""
    environment: str = "development"
    export_interval_seconds: PositiveFloat = 15.0
    # How often the scheduler process re-reads queue/outbox state for gauges.
    gauge_refresh_seconds: PositiveFloat = 15.0
    export_timeout_seconds: PositiveInt = 10

    def parsed_headers(self) -> dict[str, str]:
        pairs = (p.partition("=") for p in self.headers.split(",") if "=" in p)
        return {k.strip(): v.strip() for k, _, v in pairs if k.strip()}
