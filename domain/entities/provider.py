from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from domain.enums import ChannelType
from domain.value_objects.ids import ProviderId, TenantId


@dataclass(slots=True)
class Provider:
    """
    A saved, configured instance of a catalog provider (e.g. "Marketing
    SES"). `name` is the catalog key selecting the driver, `label` the
    tenant's own name for this instance. `config` holds non-secret settings;
    credentials live encrypted in `secrets_encrypted` (write-only).
    """

    id: ProviderId
    tenant_id: TenantId
    channel: ChannelType
    name: str
    label: str = ""
    config: dict[str, Any] = field(default_factory=dict)
    secrets_encrypted: str | None = None
    priority: int = 0
    is_active: bool = True
    created_at: datetime | None = None
    updated_at: datetime | None = None
