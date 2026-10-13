from __future__ import annotations

from config.base import ConfigSection
from pydantic import PositiveInt


class RetrySettings(ConfigSection):
    """Exponential backoff for failed delivery dispatches."""

    max_attempts: PositiveInt = 3
    base_delay_seconds: float = 5.0
    max_delay_seconds: float = 300.0
    # Upper bound on one provider call. A timeout counts as a hard failure,
    # so the workflow's next provider for the channel is tried.
    provider_timeout_seconds: float = 30.0
