from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from domain.enums import NotificationStatus
from domain.value_objects.ids import EventId, NotificationId, TenantId, WorkflowId


@dataclass(slots=True)
class Notification:
    """
    Aggregate root: one logical notification produced from an event.
    Fan-out to recipients and channels lives in DeliveryAttempt rows.
    """

    id: NotificationId
    tenant_id: TenantId
    event_id: EventId
    workflow_id: WorkflowId
    status: NotificationStatus = NotificationStatus.CREATED
    payload: dict[str, Any] = field(default_factory=dict)
    # When set and in the future, fan-out waits until this instant
    # (status stays CREATED; the scheduler dispatches it when due).
    scheduled_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def mark_processing(self) -> None:
        self.status = NotificationStatus.PROCESSING

    def mark_dispatched(self) -> None:
        self.status = NotificationStatus.DISPATCHED

    def mark_completed(self) -> None:
        self.status = NotificationStatus.COMPLETED

    def mark_failed(self) -> None:
        self.status = NotificationStatus.FAILED
