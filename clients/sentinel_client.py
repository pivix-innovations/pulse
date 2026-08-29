from __future__ import annotations

from typing import Any, Mapping

from domain.value_objects.ids import TenantId
from services.ports.clients import ResolvedRecipient


class InMemorySentinelClient:
    """
    Stub for the Sentinel user/tenant service, which doesn't exist yet.

    Resolution order:
    1. If the event payload carries a `recipients` list, use it directly
       (each item: {"external_ref", "email", "phone", "locale", "timezone"}).
    2. Otherwise fall back to recipients registered on this stub instance.
    """

    def __init__(self) -> None:
        self._directory: dict[str, list[ResolvedRecipient]] = {}

    def register(self, tenant_id: TenantId, recipient: ResolvedRecipient) -> None:
        self._directory.setdefault(str(tenant_id), []).append(recipient)

    async def resolve_recipients(
        self, tenant_id: TenantId, event_type: str, payload: Mapping[str, Any]
    ) -> list[ResolvedRecipient]:
        inline = payload.get("recipients")
        if isinstance(inline, list) and inline:
            return [
                ResolvedRecipient(
                    external_ref=str(item["external_ref"]),
                    email=item.get("email"),
                    phone=item.get("phone"),
                    locale=item.get("locale", "en"),
                    timezone=item.get("timezone", "UTC"),
                )
                for item in inline
                if isinstance(item, dict) and "external_ref" in item
            ]
        return list(self._directory.get(str(tenant_id), []))
