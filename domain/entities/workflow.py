from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from domain.value_objects.ids import TenantId, WorkflowId


@dataclass(slots=True)
class Workflow:
    """
    Maps an event type to how it should be notified: channels, templates,
    fallback/escalation rules. `definition` holds the declarative body
    (channel list, per-channel template refs, escalation chain and cap).
    """

    id: WorkflowId
    tenant_id: TenantId
    name: str
    event_type: str
    definition: dict[str, Any] = field(default_factory=dict)
    is_active: bool = True
    created_at: datetime | None = None
    updated_at: datetime | None = None
