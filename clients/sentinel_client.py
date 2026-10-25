from __future__ import annotations

from typing import Any, Mapping

from domain.value_objects.ids import TenantId
import logging

from services.ports.clients import ResolvedRecipient, SentinelClient, SentinelUnavailable
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


logger = logging.getLogger(__name__)


class DatabaseRecipientClient:
    """
    Resolves who to notify, in this order:

    1. recipients carried inline in the event payload (no lookups at all);
    2. Sentinel, when configured (`remote`): authoritative, cached and
       resilient. If Sentinel says there is nobody, there is nobody;
    3. the local `recipients` snapshot, when the payload names one by
       `recipient_id` / `user_id` / `external_ref`. Without Sentinel this is
       the normal path; with Sentinel it is the fallback while Sentinel is
       unreachable (the snapshot is refreshed from Sentinel at every fan-out).

    If Sentinel is unreachable and nothing local matches, SentinelUnavailable
    propagates so the caller retries instead of dropping the event.
    """

    _REF_KEYS = ("recipient_id", "user_id", "external_ref")

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        inner: SentinelClient | None = None,
        remote: SentinelClient | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._inner = inner or InMemorySentinelClient()
        self._remote = remote

    async def resolve_recipients(
        self, tenant_id: TenantId, event_type: str, payload: Mapping[str, Any]
    ) -> list[ResolvedRecipient]:
        resolved = await self._inner.resolve_recipients(tenant_id, event_type, payload)
        if resolved:
            return resolved

        if self._remote is not None:
            try:
                return await self._remote.resolve_recipients(tenant_id, event_type, payload)
            except SentinelUnavailable as exc:
                local = await self._local(tenant_id, payload)
                if local:
                    logger.warning("Sentinel unavailable (%s); using the local recipient snapshot", exc)
                    return local
                raise
        return await self._local(tenant_id, payload)

    async def _local(self, tenant_id: TenantId, payload: Mapping[str, Any]) -> list[ResolvedRecipient]:
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


def build_recipient_client(uow_factory: UnitOfWorkFactory, settings) -> DatabaseRecipientClient:
    """The recipient resolver for this deployment: with Sentinel if configured, else local only."""
    if not settings.sentinel.base_url:
        return DatabaseRecipientClient(uow_factory)
    from clients.sentinel_http import HttpSentinelClient, ResilientSentinelClient

    remote = ResilientSentinelClient(HttpSentinelClient(settings.sentinel), settings.sentinel)
    return DatabaseRecipientClient(uow_factory, remote=remote)
