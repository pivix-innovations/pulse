from __future__ import annotations

from config.base import ConfigSection
from pydantic import PositiveInt


class OutboxSettings(ConfigSection):
    poll_interval_seconds: float = 1.0
    batch_size: PositiveInt = 100
