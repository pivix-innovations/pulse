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


class ProviderSendError(Exception):
    """
    Raised when a provider rejects or fails to accept a message.
    `retryable=False` marks failures that retrying cannot fix (e.g. no
    live SSE connection, invalid destination) -- they skip backoff and go
    straight to failed, where the reconciliation sweep may escalate them.
    """

    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


class NotificationProvider(Protocol):
    """Common interface every channel driver implements."""

    name: str
    channel: ChannelType

    async def send(self, message: OutboundMessage) -> SendResult: ...
