from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from domain.value_objects.ids import TenantId
from services.ports.uow import UnitOfWorkFactory


@dataclass(slots=True)
class MetricsSummary:
    window_hours: int
    notifications: dict[str, int] = field(default_factory=dict)
    attempts: dict[str, dict[str, int]] = field(default_factory=dict)
    dead_letters: int = 0
    # Mean sent -> delivered latency over confirmed attempts; None if none.
    avg_confirmation_seconds: float | None = None
    # Attempts over time: width of each bucket, and (bucket start, status counts) for the non-empty buckets.
    bucket_seconds: int = 3600
    timeline: list[tuple[datetime, dict[str, int]]] = field(default_factory=list)


# Bucket widths that give a readable chart (about 24-60 bars) for any window.
_BUCKETS = (60, 300, 900, 1800, 3600, 7200, 21600, 86400)


def bucket_for(window_hours: int) -> int:
    """The narrowest nice bucket that keeps a window to at most ~60 bars."""
    return next((b for b in _BUCKETS if window_hours * 3600 / b <= 60), _BUCKETS[-1])


class MetricsService:
    """Tenant-scoped delivery metrics over a trailing window."""

    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def summary(self, tenant_id: TenantId, window_hours: int = 24) -> MetricsSummary:
        since = datetime.now(timezone.utc) - timedelta(hours=window_hours)
        result = MetricsSummary(window_hours=window_hours)
        async with self._uow_factory() as uow:
            for status, count in await uow.metrics.notifications_by_status(tenant_id, since):
                result.notifications[status] = count
            for channel, status, count in await uow.metrics.attempts_by_channel_status(
                tenant_id, since
            ):
                result.attempts.setdefault(channel.value, {})[status.value] = count
            result.dead_letters = await uow.metrics.dead_letter_count(tenant_id, since)
            result.avg_confirmation_seconds = await uow.metrics.avg_confirmation_seconds(
                tenant_id, since
            )
            result.bucket_seconds = bucket_for(window_hours)
            by_bucket: dict[datetime, dict[str, int]] = {}
            for start, status, count in await uow.metrics.attempts_timeline(
                tenant_id, since, result.bucket_seconds
            ):
                by_bucket.setdefault(start, {})[status.value] = count
            result.timeline = sorted(by_bucket.items())
        return result
