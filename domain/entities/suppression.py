from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from domain.enums import ChannelType
from domain.value_objects.ids import SuppressionId, TenantId


class SuppressionReason(str, Enum):
    BOUNCE = "bounce"
    COMPLAINT = "complaint"
    UNSUBSCRIBE = "unsubscribe"
    MANUAL = "manual"


def normalize_address(channel: ChannelType, address: str) -> str:
    """Email is case-insensitive; phone numbers compare without spaces."""
    address = address.strip()
    return address.lower() if channel is ChannelType.EMAIL else address.replace(" ", "")


@dataclass(slots=True)
class Suppression:
    """An address Pulse must not send to on a channel, whoever the recipient is."""

    id: SuppressionId
    tenant_id: TenantId
    channel: ChannelType
    address: str
    reason: SuppressionReason
    detail: str | None = None
    created_at: datetime | None = None
