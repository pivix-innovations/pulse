from __future__ import annotations

from enum import Enum


class DeliveryStatus(str, Enum):
    """
    Lifecycle of a single delivery attempt.

    `SENT` means the provider accepted the message; `DELIVERED` means the
    receiving end confirmed it (client ack or provider callback). The two
    are deliberately distinct.
    """

    PENDING = "pending"
    SENT = "sent"
    DELIVERED = "delivered"
    FAILED = "failed"
    RETRYING = "retrying"
    DEAD = "dead"


# Allowed state transitions: pending -> sent -> delivered | failed | retrying | dead
DELIVERY_TRANSITIONS: dict[DeliveryStatus, frozenset[DeliveryStatus]] = {
    DeliveryStatus.PENDING: frozenset(
        {DeliveryStatus.SENT, DeliveryStatus.FAILED, DeliveryStatus.RETRYING, DeliveryStatus.DEAD}
    ),
    DeliveryStatus.SENT: frozenset(
        {DeliveryStatus.DELIVERED, DeliveryStatus.FAILED, DeliveryStatus.RETRYING, DeliveryStatus.DEAD}
    ),
    DeliveryStatus.RETRYING: frozenset(
        {DeliveryStatus.SENT, DeliveryStatus.FAILED, DeliveryStatus.DEAD}
    ),
    DeliveryStatus.FAILED: frozenset({DeliveryStatus.RETRYING, DeliveryStatus.DEAD}),
    DeliveryStatus.DELIVERED: frozenset(),
    DeliveryStatus.DEAD: frozenset(),
}
