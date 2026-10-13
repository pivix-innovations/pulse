from __future__ import annotations

import logging
from dataclasses import dataclass

from redis.asyncio import Redis

from config.settings import Settings
from domain.entities.provider import Provider
from domain.enums import ChannelType
from domain.value_objects.ids import ProviderId, TenantId, WorkflowId
from providers.catalog import BuildContext, get_spec
from providers.email.factory import build_email_provider
from providers.in_app.sse import SseInAppProvider
from providers.push.factory import build_push_provider
from providers.sms.factory import build_sms_provider
from services.ports.providers import NotificationProvider
from services.ports.uow import UnitOfWork
from services.secrets import SecretBox
from sse.broadcaster import SseBroadcaster

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ResolvedProvider:
    provider: NotificationProvider
    provider_id: ProviderId | None  # None for the env-configured default
    label: str


class ProviderRegistry:
    """
    Resolves the ordered provider chain for a delivery.

    1. The workflow's own chain (`definition.provider_chains[channel]`, saved
       providers in priority order), skipping any that are inactive,
       deleted or unbuildable.
    2. Otherwise the tenant's highest-priority active saved provider for the
       channel (how things worked before workflows had chains).
    3. Otherwise the env-configured default.
    """

    def __init__(
        self,
        settings: Settings,
        redis: Redis | None = None,
        secret_box: SecretBox | None = None,
    ) -> None:
        self._settings = settings
        self._redis = redis
        self._box = secret_box or SecretBox(settings.security.secrets_key)
        self._defaults: dict[ChannelType, NotificationProvider] = {}
        # Built drivers, keyed so an edited row rebuilds its driver.
        self._built: dict[tuple[ProviderId, object], NotificationProvider] = {}

    def get(self, channel: ChannelType) -> NotificationProvider:
        """The env-configured default driver for the channel."""
        if channel not in self._defaults:
            self._defaults[channel] = self._build_default(channel)
        return self._defaults[channel]

    async def resolve_chain(
        self,
        uow: UnitOfWork,
        tenant_id: TenantId,
        channel: ChannelType,
        workflow_id: WorkflowId | None = None,
    ) -> list[ResolvedProvider]:
        if workflow_id is not None:
            chain = await self._workflow_chain(uow, tenant_id, channel, workflow_id)
            if chain:
                return chain

        stored = await uow.providers.get_active_by_channel(tenant_id, channel)
        if stored is not None:
            built = self._build(stored)
            if built is not None:
                return [ResolvedProvider(built, stored.id, stored.label or stored.name)]
        return [ResolvedProvider(self.get(channel), None, "default")]

    async def resolve(
        self, uow: UnitOfWork, tenant_id: TenantId, channel: ChannelType
    ) -> tuple[NotificationProvider, ProviderId | None]:
        first = (await self.resolve_chain(uow, tenant_id, channel))[0]
        return first.provider, first.provider_id

    async def _workflow_chain(
        self, uow: UnitOfWork, tenant_id: TenantId, channel: ChannelType, workflow_id: WorkflowId
    ) -> list[ResolvedProvider]:
        workflow = await uow.workflows.get(tenant_id, workflow_id)
        if workflow is None:
            return []
        raw_ids = (workflow.definition.get("provider_chains") or {}).get(channel.value) or []
        try:
            ordered = [ProviderId.from_str(i) for i in raw_ids]
        except ValueError:
            logger.warning("Workflow %s has a malformed provider chain", workflow_id)
            return []
        rows = {p.id: p for p in await uow.providers.list_by_ids(tenant_id, ordered)}
        chain: list[ResolvedProvider] = []
        for pid in ordered:
            row = rows.get(pid)
            if row is None or not row.is_active or row.channel is not channel:
                continue
            built = self._build(row)
            if built is not None:
                chain.append(ResolvedProvider(built, row.id, row.label or row.name))
        if raw_ids and not chain:
            logger.warning(
                "Workflow %s: no usable provider in its %s chain; using the tenant default",
                workflow_id,
                channel.value,
            )
        return chain

    def _build(self, row: Provider) -> NotificationProvider | None:
        key = (row.id, row.updated_at)
        cached = self._built.get(key)
        if cached is not None:
            return cached
        spec = get_spec(row.channel, row.name)
        if spec is None:
            logger.warning(
                "No catalog entry '%s' for channel '%s' (provider %s); skipping",
                row.name,
                row.channel.value,
                row.id,
            )
            return None
        try:
            secrets = self._box.decrypt(row.secrets_encrypted)
            provider = spec.build(
                dict(row.config),
                secrets,
                BuildContext(
                    redis=self._redis,
                    fcm_credentials_path=self._settings.push.fcm_credentials_path,
                ),
            )
        except Exception:
            logger.exception("Cannot build saved provider %s; skipping", row.id)
            return None
        self._built[key] = provider
        return provider

    def _build_default(self, channel: ChannelType) -> NotificationProvider:
        if channel is ChannelType.EMAIL:
            return build_email_provider(self._settings.email)
        if channel is ChannelType.PUSH:
            return build_push_provider(self._settings.push)
        if channel is ChannelType.SMS:
            return build_sms_provider()
        if channel is ChannelType.IN_APP:
            if self._redis is None:
                raise LookupError("in_app provider requires a Redis connection")
            return SseInAppProvider(SseBroadcaster(self._redis))
        raise LookupError(f"No provider registered for channel '{channel.value}'")
