from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from domain.enums import ChannelType
from domain.value_objects.ids import InvalidIdError, NotificationId, RecipientId


class WebhookParseError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class WebhookUpdate:
    """A provider callback mapped onto our delivery vocabulary."""

    notification_id: NotificationId
    recipient_id: RecipientId
    channel: ChannelType
    delivered: bool  # False -> failure (bounce, undelivered, ...)
    reason: str | None = None


def _parse_dispatch_key(key: str) -> tuple[NotificationId, RecipientId, ChannelType]:
    try:
        notification_raw, recipient_raw, channel_raw = key.rsplit(":", 2)
        return (
            NotificationId.from_str(notification_raw),
            RecipientId.from_str(recipient_raw),
            ChannelType(channel_raw),
        )
    except (ValueError, InvalidIdError) as exc:
        raise WebhookParseError(f"Malformed idempotency key '{key}'") from exc


def _parse_generic(payload: dict[str, Any]) -> list[WebhookUpdate]:
    """
    Generic/console vocabulary:
    {"idempotency_key": "<nid>:<rid>:<channel>", "status": "delivered"|"bounced"|"failed",
     "reason": "..."}.
    """
    key = payload.get("idempotency_key")
    status = payload.get("status")
    if not isinstance(key, str) or status not in ("delivered", "bounced", "failed"):
        raise WebhookParseError("Expected 'idempotency_key' and a known 'status'")
    notification_id, recipient_id, channel = _parse_dispatch_key(key)
    return [
        WebhookUpdate(
            notification_id=notification_id,
            recipient_id=recipient_id,
            channel=channel,
            delivered=status == "delivered",
            reason=payload.get("reason") or (None if status == "delivered" else status),
        )
    ]


# Provider-specific parsers map each provider's status vocabulary onto our
# DeliveryAttempt states HERE, not in the endpoint. SES/Resend/Twilio/MSG91
# parsers slot in alongside the generic one when those drivers land.
_PARSERS = {
    "console": _parse_generic,
    "generic": _parse_generic,
}


def known_provider(provider: str) -> bool:
    return provider in _PARSERS


def parse_webhook(provider: str, payload: dict[str, Any]) -> list[WebhookUpdate]:
    parser = _PARSERS.get(provider)
    if parser is None:
        raise WebhookParseError(f"Unknown webhook provider '{provider}'")
    return parser(payload)
