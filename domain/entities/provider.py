from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from domain.enums import ChannelType
from domain.value_objects.ids import ProviderId, TenantId


@dataclass(slots=True)
class Provider:
    """
    A configured delivery provider (e.g. smtp, ses, fcm, twilio) for a
    channel. `config` holds non-secret settings only; credentials come
    from the environment.
    """

    id: ProviderId
    tenant_id: TenantId
    channel: ChannelType
    name: str
    config: dict[str, Any] = field(default_factory=dict)
    priority: int = 0
    is_active: bool = True
    created_at: datetime | None = None
    updated_at: datetime | None = None
