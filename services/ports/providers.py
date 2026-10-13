from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from domain.enums import ChannelType


@dataclass(frozen=True, slots=True)
class OutboundMessage:
    """Channel-agnostic message handed to a provider driver."""

    channel: ChannelType
    to: str
    body_text: str
    subject: str | None = None
    body_html: str | None = None
    # Carried through to the provider dispatch so retries can be deduplicated.
    idempotency_key: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SendResult:
    """Provider acceptance only -- this is `sent`, never `delivered`."""

    provider_message_id: str | None = None
    # Destinations the provider reported permanently dead (e.g. unregistered
    # push tokens), for the caller to prune.
    invalid_destinations: tuple[str, ...] = ()


class ProviderSendError(Exception):
    """
    Raised when a provider rejects or fails to accept a message.
    `retryable=False` marks failures that retrying cannot fix (e.g. no
    live SSE connection, invalid destination) -- they skip backoff and go
    straight to failed, where the reconciliation sweep may escalate them.
    `failover=False` additionally stops the provider chain: the next
    provider would fail the same way.
    """

    def __init__(
        self,
        message: str,
        *,
        retryable: bool = True,
        failover: bool = True,
        invalid_destinations: tuple[str, ...] = (),
    ) -> None:
        super().__init__(message)
        self.invalid_destinations = invalid_destinations
        self.retryable = retryable
        # False when another provider for the same channel cannot do better
        # (bad destination, no live connection): the failure is the
        # recipient's, not this provider's.
        self.failover = failover


class NotificationProvider(Protocol):
    """Common interface every channel driver implements."""

    name: str
    channel: ChannelType
    # False when the provider has no delivery callback/ack path, making
    # `sent` the terminal state for its attempts.
    confirms_delivery: bool

    async def send(self, message: OutboundMessage) -> SendResult: ...
