from __future__ import annotations

from config.base import ConfigSection
from pydantic import PositiveInt


class RetrySettings(ConfigSection):
    """Exponential backoff for failed delivery dispatches."""

    max_attempts: PositiveInt = 3
    base_delay_seconds: float = 5.0
    max_delay_seconds: float = 300.0
