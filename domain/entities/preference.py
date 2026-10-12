from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from domain.enums import ChannelType
from domain.value_objects.ids import PreferenceId, RecipientId, TenantId
from domain.value_objects.quiet_hours import QuietHours


@dataclass(slots=True)
class Preference:
    """A recipient's opt-in state and quiet hours for one channel."""

    id: PreferenceId
    tenant_id: TenantId
    recipient_id: RecipientId
    channel: ChannelType
    opted_in: bool = True
    quiet_hours: QuietHours | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
