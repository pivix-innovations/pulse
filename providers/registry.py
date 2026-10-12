from __future__ import annotations

import logging

from redis.asyncio import Redis

from config.email import EmailSettings
from config.push import PushSettings
from config.settings import Settings
from domain.entities.provider import Provider
from domain.enums import ChannelType
from domain.value_objects.ids import ProviderId, TenantId
from providers.email.factory import build_email_provider
from providers.in_app.sse import SseInAppProvider
from providers.push.factory import build_push_provider
from providers.sms.factory import build_sms_provider
from services.ports.providers import NotificationProvider
from services.ports.uow import UnitOfWork
from sse.broadcaster import SseBroadcaster

logger = logging.getLogger(__name__)


class ProviderRegistry:
    """
    Resolves the driver for a (tenant, channel). A tenant's active row in
    the `providers` table wins (its `name` picks the driver, its non-secret
    `config` overrides the env settings); otherwise the env-configured
    default is used. Credentials always come from the environment.
    """

    def __init__(self, settings: Settings, redis: Redis | None = None) -> None:
        self._settings = settings
        self._redis = redis
        self._defaults: dict[ChannelType, NotificationProvider] = {}
        # Stored-row drivers, keyed so an edited row rebuilds its driver.
        self._stored: dict[tuple[ProviderId, object], NotificationProvider] = {}

    def get(self, channel: ChannelType) -> NotificationProvider:
        """The env-configured default driver for the channel."""
        if channel not in self._defaults:
            self._defaults[channel] = self._build_default(channel)
        return self._defaults[channel]

    async def resolve(
        self, uow: UnitOfWork, tenant_id: TenantId, channel: ChannelType
    ) -> tuple[NotificationProvider, ProviderId | None]:
        stored = await uow.providers.get_active_by_channel(tenant_id, channel)
        if stored is not None:
            key = (stored.id, stored.updated_at)
            provider = self._stored.get(key)
            if provider is None:
                provider = self._build_stored(stored)
                if provider is not None:
                    self._stored[key] = provider
            if provider is not None:
                return provider, stored.id
        return self.get(channel), None

    def _build_stored(self, row: Provider) -> NotificationProvider | None:
        try:
            if row.channel is ChannelType.EMAIL and row.name in ("smtp", "console"):
                base = self._settings.email.model_dump()
                overrides = {k: v for k, v in row.config.items() if k in base and k != "driver"}
                return build_email_provider(
                    EmailSettings(**{**base, **overrides, "driver": row.name})
                )
            if row.channel is ChannelType.PUSH and row.name in ("fcm", "console"):
                base = self._settings.push.model_dump()
                overrides = {k: v for k, v in row.config.items() if k in base and k != "driver"}
                return build_push_provider(
                    PushSettings(**{**base, **overrides, "driver": row.name})
                )
            if row.channel is ChannelType.SMS and row.name == "console":
                return build_sms_provider()
        except Exception:
            logger.exception("Invalid stored provider %s; using the default", row.id)
            return None
        logger.warning(
            "No driver '%s' for channel '%s' (provider %s); using the default",
            row.name,
            row.channel.value,
            row.id,
        )
        return None

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
