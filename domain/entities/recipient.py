from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from domain.value_objects.ids import RecipientId, TenantId


@dataclass(slots=True)
class Recipient:
    """
    Local snapshot of a recipient. Sentinel owns the canonical user record;
    `external_ref` is the Sentinel user id this row mirrors.
    """

    id: RecipientId
    tenant_id: TenantId
    external_ref: str
    email: str | None = None
    phone: str | None = None
    locale: str = "en"
    timezone: str = "UTC"
    created_at: datetime | None = None
    updated_at: datetime | None = None
