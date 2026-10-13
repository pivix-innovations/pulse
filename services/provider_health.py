from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from services.ports.repositories import ProviderCallStats

HealthStatus = Literal["healthy", "degraded", "down", "idle"]

MIN_CALLS_FOR_RATE = 5  # below this a single failure shouldn't paint a provider red
DEGRADED_BELOW = 0.95
DOWN_BELOW = 0.5
DOWN_STREAK = 3  # this many most-recent calls all failing means it is down *now*


@dataclass(frozen=True, slots=True)
class ProviderHealth:
    status: HealthStatus
    calls: int
    failures: int
    success_rate: float | None
    avg_latency_ms: float | None
    p95_latency_ms: float | None
    last_success_at: datetime | None
    last_failure_at: datetime | None
    last_error: str | None


def assess(stats: ProviderCallStats | None) -> ProviderHealth:
    """
    Provider health from its recent calls. Recipient errors (bad numbers,
    refused addresses) never reach here, so a provider is never blamed for
    its customers' bad data.
    """
    if stats is None or stats.calls == 0:
        return ProviderHealth("idle", 0, 0, None, None, None, None, None, None)

    rate = 1 - stats.failures / stats.calls
    streak = stats.recent[:DOWN_STREAK]
    if len(streak) == DOWN_STREAK and all(o == "provider_error" for o in streak):
        status: HealthStatus = "down"
    elif stats.calls >= MIN_CALLS_FOR_RATE and rate < DOWN_BELOW:
        status = "down"
    elif stats.calls >= MIN_CALLS_FOR_RATE and rate < DEGRADED_BELOW:
        status = "degraded"
    elif stats.calls < MIN_CALLS_FOR_RATE and stats.failures:
        status = "degraded"
    else:
        status = "healthy"

    return ProviderHealth(
        status=status,
        calls=stats.calls,
        failures=stats.failures,
        success_rate=round(rate, 4),
        avg_latency_ms=stats.avg_latency_ms,
        p95_latency_ms=stats.p95_latency_ms,
        last_success_at=stats.last_success_at,
        last_failure_at=stats.last_failure_at,
        last_error=stats.last_error,
    )
