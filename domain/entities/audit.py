from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from domain.value_objects.ids import AuditEntryId, TenantId


@dataclass(slots=True)
class AuditEntry:
    """Who changed what. Holds ids and the action only: never request bodies, so secrets cannot leak into it."""

    id: AuditEntryId
    tenant_id: TenantId
    actor_type: str            # "user" | "api_key" | "env_key"
    actor_id: str | None
    actor_label: str | None    # user email
    action: str                # e.g. "workflow.update"
    resource_type: str
    resource_id: str | None
    detail: dict[str, Any] = field(default_factory=dict)
    created_at: datetime | None = None
