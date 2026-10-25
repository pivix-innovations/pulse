from __future__ import annotations

from config.base import ConfigSection
from pydantic import PositiveFloat, PositiveInt


class RateLimitSettings(ConfigSection):
    """
    Ingress limits for POST /events, per tenant. A tenant can override each
    value (platform admin); unset overrides use these defaults.

    - rate/burst: token bucket. `burst` events may arrive at once, refilling
      at `rate_per_second`.
    - daily_quota: events accepted per UTC day; None = unlimited.
    """

    enabled: bool = True
    default_rate_per_second: PositiveFloat = 50.0
    default_burst: PositiveInt = 100
    default_daily_quota: PositiveInt | None = None
    # How long a tenant's resolved limits are cached in-process.
    limits_cache_seconds: PositiveFloat = 30.0
