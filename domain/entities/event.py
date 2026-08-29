from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from domain.value_objects.ids import EventId, TenantId


@dataclass(slots=True)
class Event:
    """An inbound event received from a producer such as Quotient."""

    id: EventId
    tenant_id: TenantId
    event_type: str
    source: str
    payload: dict[str, Any] = field(default_factory=dict)
    idempotency_key: str | None = None
    occurred_at: datetime | None = None
    received_at: datetime | None = None
