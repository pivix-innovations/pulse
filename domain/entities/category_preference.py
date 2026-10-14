from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from domain.value_objects.ids import CategoryPreferenceId, RecipientId, TenantId

ALL_CHANNELS = "all"


@dataclass(slots=True)
class CategoryPreference:
    """
    A recipient's choice for one category of message (e.g. "marketing", "product-updates"), on one channel
    or on all of them. Only workflows that declare a `category` are affected.
    """

    id: CategoryPreferenceId
    tenant_id: TenantId
    recipient_id: RecipientId
    category: str
    channel: str = ALL_CHANNELS      # a ChannelType value, or "all"
    opted_in: bool = True
    created_at: datetime | None = None
    updated_at: datetime | None = None
