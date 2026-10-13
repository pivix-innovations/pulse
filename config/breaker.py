from __future__ import annotations

from config.base import ConfigSection
from pydantic import PositiveInt


class BreakerSettings(ConfigSection):
    """
    Circuit breaker per saved provider. After `failure_threshold` consecutive
    provider failures (within `window_seconds` of each other) the provider is
    skipped for `cooldown_seconds`; then one probe message tests it.
    """

    enabled: bool = True
    failure_threshold: PositiveInt = 5
    window_seconds: PositiveInt = 300
    cooldown_seconds: PositiveInt = 60
    probe_lease_seconds: PositiveInt = 30
