from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from domain.enums import ChannelType
from domain.value_objects.ids import (
    DeadLetterId,
    DeliveryAttemptId,
    NotificationId,
    TenantId,
)


@dataclass(slots=True)
class DeadLetter:
    """
    Terminal record of a delivery attempt that exhausted retries or could
    not be confirmed/escalated. Kept in its own table so operators can
    inspect and replay failures.
    """

    id: DeadLetterId
    tenant_id: TenantId
    attempt_id: DeliveryAttemptId
    notification_id: NotificationId
    channel: ChannelType
    reason: str
    created_at: datetime | None = None
