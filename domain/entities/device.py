from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from domain.enums import DevicePlatform
from domain.value_objects.ids import DeviceTokenId, RecipientId, TenantId


@dataclass(slots=True)
class DeviceToken:
    """
    A push-capable device registration. `last_seen_at` drives pruning of
    stale tokens.
    """

    id: DeviceTokenId
    tenant_id: TenantId
    recipient_id: RecipientId
    platform: DevicePlatform
    token: str
    last_seen_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
