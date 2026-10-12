from __future__ import annotations

from enum import Enum


class NotificationStatus(str, Enum):
    """Lifecycle of the notification aggregate (fan-out level, not per-attempt)."""

    CREATED = "created"
    PROCESSING = "processing"
    DISPATCHED = "dispatched"
    COMPLETED = "completed"
    FAILED = "failed"
