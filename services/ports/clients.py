from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from domain.value_objects.ids import TenantId


@dataclass(frozen=True, slots=True)
class ResolvedRecipient:
    """Recipient data as returned by Sentinel (not yet a local Recipient row)."""

    external_ref: str
    email: str | None = None
    phone: str | None = None
    locale: str = "en"
    timezone: str = "UTC"


class SentinelClient(Protocol):
    """Port for the Sentinel user/tenant service (stubbed until it exists)."""

    async def resolve_recipients(
        self, tenant_id: TenantId, event_type: str, payload: Mapping[str, Any]
    ) -> list[ResolvedRecipient]: ...
