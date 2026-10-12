from __future__ import annotations

from config.base import ConfigSection
from pydantic import PositiveInt


class ReconciliationSettings(ConfigSection):
    """
    The sweep finds attempts stuck in `sent` past a per-channel TTL with no
    delivery confirmation and escalates them to the workflow's fallback
    channel (or dead-letters them).
    """

    interval_seconds: float = 30.0
    batch_size: PositiveInt = 100
    sse_ttl_seconds: PositiveInt = 120
    push_ttl_seconds: PositiveInt = 300
    sms_ttl_seconds: PositiveInt = 900
    email_ttl_seconds: PositiveInt = 3600
    # Pending/retrying attempts nobody worked: queue job lost (never claimed)
    # or worker died mid-send (claimed, no outcome). Must exceed the dispatch
    # guard lease so a requeued job isn't rejected as a duplicate.
    stuck_lost_ttl_seconds: PositiveInt = 300
    stuck_in_doubt_ttl_seconds: PositiveInt = 300
