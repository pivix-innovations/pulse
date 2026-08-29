from __future__ import annotations

from config.push import PushSettings
from providers.push.base import PushProvider
from providers.push.console import ConsolePushProvider
from providers.push.firebase import FcmPushProvider


def build_push_provider(settings: PushSettings) -> PushProvider:
    if settings.driver == "fcm":
        return FcmPushProvider(settings)
    # Console keeps the pipeline runnable locally without credentials.
    return ConsolePushProvider()
