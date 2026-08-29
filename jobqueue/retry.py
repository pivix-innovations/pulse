from __future__ import annotations

import random

from config.retry import RetrySettings


def compute_backoff(attempt_count: int, settings: RetrySettings) -> float:
    """
    Exponential backoff with full jitter: base * 2^(n-1), capped, then
    jittered to avoid thundering herds.
    """
    exponential = settings.base_delay_seconds * (2 ** max(attempt_count - 1, 0))
    capped = min(exponential, settings.max_delay_seconds)
    return capped * (0.5 + random.random() / 2)
