from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from domain.value_objects.ids import DeliveryAttemptId, InboxMessageId, NotificationId, RecipientId, TenantId


@dataclass(slots=True)
class InboxMessage:
    """
    A recipient's persistent in-app message. Written when the in_app attempt
    is dispatched (one per attempt), so it is there whether or not the
    recipient was online for the live push.
    """

    id: InboxMessageId
    tenant_id: TenantId
    recipient_id: RecipientId
    notification_id: NotificationId
    attempt_id: DeliveryAttemptId
    title: str | None
    body: str
    read_at: datetime | None = None
    archived_at: datetime | None = None
    created_at: datetime | None = None

    @property
    def is_read(self) -> bool:
        return self.read_at is not None

    @property
    def is_archived(self) -> bool:
        return self.archived_at is not None
