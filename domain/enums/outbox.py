from __future__ import annotations

from enum import Enum


class OutboxStatus(str, Enum):
    PENDING = "pending"
    PUBLISHED = "published"
