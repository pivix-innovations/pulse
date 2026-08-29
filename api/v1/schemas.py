from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from domain.enums import ChannelType, DeliveryStatus, NotificationStatus


class EventIn(BaseModel):
    event_type: str = Field(min_length=1, max_length=200)
    source: str = Field(min_length=1, max_length=100)
    payload: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = Field(default=None, max_length=255)
    occurred_at: datetime | None = None
    # Future instant -> the notification is created now but fanned out then.
    scheduled_at: datetime | None = None


class EventAccepted(BaseModel):
    notification_id: uuid.UUID
    status: str = "accepted"


class DeliveryAttemptOut(BaseModel):
    id: uuid.UUID
    recipient_id: uuid.UUID
    channel: ChannelType
    status: DeliveryStatus
    attempt_count: int
    last_error: str | None
    sent_at: datetime | None
    delivered_at: datetime | None


class NotificationOut(BaseModel):
    id: uuid.UUID
    status: NotificationStatus
    event_id: uuid.UUID
    workflow_id: uuid.UUID
    created_at: datetime | None
    attempts: list[DeliveryAttemptOut]
