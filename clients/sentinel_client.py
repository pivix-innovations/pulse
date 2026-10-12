from __future__ import annotations

from typing import Any, Mapping

from domain.value_objects.ids import TenantId
from services.ports.clients import ResolvedRecipient, SentinelClient
from services.ports.uow import UnitOfWorkFactory


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


class DatabaseRecipientClient:
    """
    Resolves recipients from inline payload data first (via the wrapped
    client), then from the local `recipients` table when the payload names
    one by `recipient_id` / `user_id` / `external_ref`.
    """

    _REF_KEYS = ("recipient_id", "user_id", "external_ref")

    def __init__(self, uow_factory: UnitOfWorkFactory, inner: SentinelClient | None = None) -> None:
        self._uow_factory = uow_factory
        self._inner = inner or InMemorySentinelClient()

    async def resolve_recipients(
        self, tenant_id: TenantId, event_type: str, payload: Mapping[str, Any]
    ) -> list[ResolvedRecipient]:
        resolved = await self._inner.resolve_recipients(tenant_id, event_type, payload)
        if resolved:
            return resolved

        ref = next((payload[k] for k in self._REF_KEYS if payload.get(k)), None)
        if ref is None:
            return []
        async with self._uow_factory() as uow:
            recipient = await uow.recipients.get_by_external_ref(tenant_id, str(ref))
        if recipient is None:
            return []
        return [
            ResolvedRecipient(
                external_ref=recipient.external_ref,
                email=recipient.email,
                phone=recipient.phone,
                locale=recipient.locale,
                timezone=recipient.timezone,
            )
        ]
