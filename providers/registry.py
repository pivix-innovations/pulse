from __future__ import annotations

from redis.asyncio import Redis

from config.settings import Settings
from domain.enums import ChannelType
from providers.email.factory import build_email_provider
from providers.in_app.sse import SseInAppProvider
from providers.push.factory import build_push_provider
from providers.sms.factory import build_sms_provider
from services.ports.providers import NotificationProvider
from sse.broadcaster import SseBroadcaster


class ProviderRegistry:
    """Builds and caches one active driver per channel from settings."""

    def __init__(self, settings: Settings, redis: Redis | None = None) -> None:
        self._settings = settings
        self._redis = redis
        self._cache: dict[ChannelType, NotificationProvider] = {}

    def get(self, channel: ChannelType) -> NotificationProvider:
        if channel not in self._cache:
            self._cache[channel] = self._build(channel)
        return self._cache[channel]

    def _build(self, channel: ChannelType) -> NotificationProvider:
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
