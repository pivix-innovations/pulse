from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from domain.value_objects.ids import (
    DigestBatchId,
    DigestItemId,
    NotificationId,
    RecipientId,
    TenantId,
    WorkflowId,
)


class DigestStatus(str, Enum):
    OPEN = "open"          # still collecting
    FLUSHED = "flushed"    # sent as one message


@dataclass(slots=True)
class DigestBatch:
    """
    One recipient's open digest for a workflow (and key). The first buffered
    event's notification is the anchor: the consolidated message is delivered
    as that notification's attempts; the other events' notifications complete
    once it goes out.
    """

    id: DigestBatchId
    tenant_id: TenantId
    workflow_id: WorkflowId
    recipient_id: RecipientId
    digest_key: str
    anchor_notification_id: NotificationId
    flush_at: datetime
    status: DigestStatus = DigestStatus.OPEN
    item_count: int = 0
    flushed_at: datetime | None = None
    created_at: datetime | None = None


@dataclass(slots=True)
class DigestItem:
    id: DigestItemId
    tenant_id: TenantId
    batch_id: DigestBatchId
    notification_id: NotificationId
    payload: dict[str, Any] = field(default_factory=dict)
    created_at: datetime | None = None
