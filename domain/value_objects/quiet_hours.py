from __future__ import annotations

from dataclasses import dataclass
from datetime import time


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
