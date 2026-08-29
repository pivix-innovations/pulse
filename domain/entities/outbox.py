from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from domain.enums import OutboxStatus
from domain.value_objects.ids import OutboxEntryId, TenantId


@dataclass(slots=True)
class OutboxEntry:
    """
    Transactional-outbox record, written in the same transaction as the
    aggregate it announces. A relay polls PENDING rows and publishes them
    to the queue, marking them PUBLISHED.
    """

    id: OutboxEntryId
    tenant_id: TenantId
    aggregate_type: str
    aggregate_id: str
    topic: str
    payload: dict[str, Any] = field(default_factory=dict)
    status: OutboxStatus = OutboxStatus.PENDING
    created_at: datetime | None = None
    published_at: datetime | None = None

    def mark_published(self, at: datetime) -> None:
        self.status = OutboxStatus.PUBLISHED
        self.published_at = at
