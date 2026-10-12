from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Sequence

from domain.entities.delivery import DeliveryAttempt
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

    def settle(self, attempts: Sequence[DeliveryAttempt]) -> bool:
        """
        Close out a PROCESSING notification once every attempt is terminal:
        COMPLETED if any attempt reached the recipient (or there was nothing
        to send), FAILED if every attempt died. Returns True if it settled.
        """
        if self.status is not NotificationStatus.PROCESSING:
            return False
        if any(not a.is_terminal for a in attempts):
            return False
        if attempts and not any(a.reached_recipient for a in attempts):
            self.mark_failed()
        else:
            self.mark_completed()
        return True
