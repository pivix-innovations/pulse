from __future__ import annotations

from config.base import ConfigSection
from pydantic import PositiveFloat, PositiveInt


class SentinelSettings(ConfigSection):
    """
    The Sentinel user/tenant service (contract: docs/sentinel-contract.md).
    With no `base_url`, recipients come only from the event payload and the
    local recipients table.
    """

    base_url: str | None = None
    token: str | None = None                      # sent as a Bearer token
    resolve_path: str = "/v1/recipients/resolve"
    # Forward only these payload keys to Sentinel (default: the whole payload).
    payload_keys: list[str] | None = None

    connect_timeout_seconds: PositiveFloat = 2.0
    read_timeout_seconds: PositiveFloat = 5.0
    retries: int = 2                              # extra attempts on timeouts / 5xx / 429
    retry_backoff_seconds: PositiveFloat = 0.2

    cache_ttl_seconds: PositiveFloat = 60.0       # answers with recipients
    negative_cache_ttl_seconds: PositiveFloat = 10.0   # "no such recipients"
    stale_ttl_seconds: PositiveFloat = 3600.0     # serve the last good answer this long if Sentinel is down

    breaker_failure_threshold: PositiveInt = 5
    breaker_cooldown_seconds: PositiveFloat = 30.0
    max_recipients: PositiveInt = 1000            # a larger answer is rejected, not truncated
