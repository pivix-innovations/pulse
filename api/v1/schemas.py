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
    # Which saved provider carried it (None: the env default), the provider's
    # own id for the message, and whether a delivery receipt is still awaited.
    provider_id: uuid.UUID | None = None
    provider_message_id: str | None = None
    awaiting_confirmation: bool = False
    template_id: uuid.UUID | None = None
    template_version: int | None = None


class StepRunOut(BaseModel):
    """Where one recipient is in a multi-step workflow."""

    recipient_id: uuid.UUID
    step_id: str
    step_index: int
    status: str            # scheduled | done | skipped
    run_at: datetime
    executed_at: datetime | None = None
    attempt_id: uuid.UUID | None = None
    reason: str | None = None   # why a step was skipped


class DigestOut(BaseModel):
    """The digest this notification's event was folded into."""

    batch_id: uuid.UUID
    status: str                      # open (still collecting) | flushed (sent)
    flush_at: datetime               # when the digest is, or was, due
    item_count: int
    is_anchor: bool                  # the anchor's attempts carry the consolidated message
    anchor_notification_id: uuid.UUID


class NotificationOut(BaseModel):
    id: uuid.UUID
    status: NotificationStatus
    event_id: uuid.UUID
    workflow_id: uuid.UUID
    created_at: datetime | None
    attempts: list[DeliveryAttemptOut]
    steps: list[StepRunOut] = []
    digest: DigestOut | None = None


class NotificationSummaryOut(BaseModel):
    id: uuid.UUID
    status: NotificationStatus
    event_id: uuid.UUID
    workflow_id: uuid.UUID
    scheduled_at: datetime | None
    created_at: datetime | None


class EventOut(BaseModel):
    id: uuid.UUID
    event_type: str
    source: str
    payload: dict[str, Any]
    idempotency_key: str | None
    occurred_at: datetime | None
    received_at: datetime | None


class DeadLetterOut(BaseModel):
    id: uuid.UUID
    attempt_id: uuid.UUID
    notification_id: uuid.UUID
    channel: ChannelType
    reason: str
    created_at: datetime | None
