from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, time, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


@dataclass(frozen=True, slots=True)
class QuietHours:
    """
    A daily window during which a recipient should not be disturbed,
    expressed in the recipient's own timezone. Supports windows that
    wrap past midnight (e.g. 22:00 -> 07:00).
    """

    start: time
    end: time
    timezone: str = "UTC"

    def covers(self, local_time: time) -> bool:
        if self.start <= self.end:
            return self.start <= local_time < self.end
        return local_time >= self.start or local_time < self.end

    def window_end(self, now: datetime) -> datetime | None:
        """
        If `now` falls inside the window, the UTC instant it ends; else None.
        An unknown timezone name falls back to UTC.
        """
        try:
            tz = ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError):
            tz = timezone.utc
        local = now.astimezone(tz)
        if not self.covers(local.time()):
            return None
        end = local.replace(
            hour=self.end.hour, minute=self.end.minute, second=0, microsecond=0
        )
        if end <= local:
            end += timedelta(days=1)
        return end.astimezone(timezone.utc)
