from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from domain.enums import ChannelType, DeliveryStatus
from domain.enums.delivery import DELIVERY_TRANSITIONS
from domain.value_objects.ids import (
    DeliveryAttemptId,
    NotificationId,
    ProviderId,
    RecipientId,
    TenantId,
)


class InvalidDeliveryTransition(Exception):
    def __init__(self, current: DeliveryStatus, target: DeliveryStatus) -> None:
        super().__init__(f"Cannot move delivery attempt from '{current.value}' to '{target.value}'")
        self.current = current
        self.target = target


@dataclass(slots=True)
class DeliveryAttempt:
    """
    One (recipient x channel) leg of a notification's fan-out.

    `sent_at` is set when a provider accepts the message; `delivered_at`
    only when the receiving end confirms (client ack or provider callback).
    """

    id: DeliveryAttemptId
    tenant_id: TenantId
    notification_id: NotificationId
    recipient_id: RecipientId
    channel: ChannelType
    status: DeliveryStatus = DeliveryStatus.PENDING
    provider_id: ProviderId | None = None
    attempt_count: int = 0
    subject: str | None = None
    body: str | None = None
    body_html: str | None = None
    last_error: str | None = None
    sent_at: datetime | None = None
    delivered_at: datetime | None = None
    next_retry_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @property
    def idempotency_key(self) -> str:
        return f"{self.notification_id}:{self.recipient_id}:{self.channel.value}"

    def _transition(self, target: DeliveryStatus) -> None:
        if target not in DELIVERY_TRANSITIONS[self.status]:
            raise InvalidDeliveryTransition(self.status, target)
        self.status = target

    def mark_sent(self, at: datetime, provider_id: ProviderId | None = None) -> None:
        self._transition(DeliveryStatus.SENT)
        self.attempt_count += 1
        self.sent_at = at
        if provider_id is not None:
            self.provider_id = provider_id

    def mark_delivered(self, at: datetime) -> None:
        self._transition(DeliveryStatus.DELIVERED)
        self.delivered_at = at

    def mark_failed(self, error: str) -> None:
        self._transition(DeliveryStatus.FAILED)
        self.attempt_count += 1
        self.last_error = error

    def mark_retrying(self, next_retry_at: datetime) -> None:
        self._transition(DeliveryStatus.RETRYING)
        self.next_retry_at = next_retry_at

    def mark_dead(self, error: str | None = None) -> None:
        self._transition(DeliveryStatus.DEAD)
        if error is not None:
            self.last_error = error
        self.next_retry_at = None
