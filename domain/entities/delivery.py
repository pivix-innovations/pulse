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

    `dispatch_started_at` is set (and committed) just before the provider
    call and cleared once its outcome is recorded, so a set value on a
    pending/retrying attempt means "outcome unknown" -- the worker died
    mid-send. `expects_confirmation` is False when the provider has no
    delivery callback (e.g. plain SMTP), making `sent` terminal.
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
    dispatch_started_at: datetime | None = None
    expects_confirmation: bool = True
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @property
    def idempotency_key(self) -> str:
        return f"{self.notification_id}:{self.recipient_id}:{self.channel.value}"

    @property
    def is_dispatchable(self) -> bool:
        return self.status in (DeliveryStatus.PENDING, DeliveryStatus.RETRYING)

    @property
    def is_terminal(self) -> bool:
        """Nothing further will happen to this attempt."""
        if self.status is DeliveryStatus.SENT:
            return not self.expects_confirmation
        return self.status in (DeliveryStatus.DELIVERED, DeliveryStatus.DEAD)

    @property
    def reached_recipient(self) -> bool:
        """Confirmed, or accepted by a provider that never confirms."""
        return self.status is DeliveryStatus.DELIVERED or (
            self.status is DeliveryStatus.SENT and not self.expects_confirmation
        )

    def _transition(self, target: DeliveryStatus) -> None:
        if target not in DELIVERY_TRANSITIONS[self.status]:
            raise InvalidDeliveryTransition(self.status, target)
        self.status = target

    def mark_dispatching(self, at: datetime) -> None:
        if not self.is_dispatchable:
            raise InvalidDeliveryTransition(self.status, self.status)
        self.dispatch_started_at = at

    def mark_requeued(self, at: datetime) -> None:
        """A lost job was re-published; `next_retry_at` restarts the stuck clock."""
        self.next_retry_at = at
        self.dispatch_started_at = None

    def mark_sent(
        self,
        at: datetime,
        provider_id: ProviderId | None = None,
        *,
        expects_confirmation: bool = True,
    ) -> None:
        self._transition(DeliveryStatus.SENT)
        self.attempt_count += 1
        self.sent_at = at
        self.expects_confirmation = expects_confirmation
        self.dispatch_started_at = None
        if provider_id is not None:
            self.provider_id = provider_id

    def mark_delivered(self, at: datetime) -> None:
        self._transition(DeliveryStatus.DELIVERED)
        self.delivered_at = at

    def mark_failed(self, error: str) -> None:
        self._transition(DeliveryStatus.FAILED)
        self.attempt_count += 1
        self.last_error = error
        self.dispatch_started_at = None

    def mark_retrying(self, next_retry_at: datetime) -> None:
        self._transition(DeliveryStatus.RETRYING)
        self.next_retry_at = next_retry_at

    def mark_replayed(self) -> None:
        """Operator-initiated redrive of a dead attempt: back to a fresh pending state."""
        if self.status is not DeliveryStatus.DEAD:
            raise InvalidDeliveryTransition(self.status, DeliveryStatus.PENDING)
        self.status = DeliveryStatus.PENDING
        self.attempt_count = 0
        self.last_error = None
        self.next_retry_at = None
        self.dispatch_started_at = None

    def mark_dead(self, error: str | None = None) -> None:
        self._transition(DeliveryStatus.DEAD)
        if error is not None:
            self.last_error = error
        self.next_retry_at = None
        self.dispatch_started_at = None
