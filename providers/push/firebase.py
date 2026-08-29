from __future__ import annotations

import asyncio
import threading

from config.push import PushSettings
from providers.push.base import PushProvider
from services.ports.providers import OutboundMessage, ProviderSendError, SendResult

_init_lock = threading.Lock()
_initialized = False


def _ensure_app(settings: PushSettings) -> None:
    global _initialized
    with _init_lock:
        if _initialized:
            return
        import firebase_admin
        from firebase_admin import credentials

        if settings.fcm_credentials_path:
            firebase_admin.initialize_app(credentials.Certificate(settings.fcm_credentials_path))
        else:
            # Falls back to GOOGLE_APPLICATION_CREDENTIALS.
            firebase_admin.initialize_app()
        _initialized = True


class FcmPushProvider(PushProvider):
    """
    FCM HTTP v1 driver sending DATA messages (not notification messages),
    so app code runs on receipt and can call the ack endpoint -- that ack
    is what moves the attempt from `sent` to `delivered`.
    """

    name = "fcm"

    def __init__(self, settings: PushSettings) -> None:
        self._settings = settings

    async def send(self, message: OutboundMessage) -> SendResult:
        tokens: list[str] = message.metadata.get("device_tokens", [])
        if not tokens:
            raise ProviderSendError("No device tokens for recipient", retryable=False)

        _ensure_app(self._settings)
        from firebase_admin import messaging

        data = {
            "attempt_id": str(message.metadata.get("attempt_id") or ""),
            "notification_id": str(message.metadata.get("notification_id") or ""),
            "title": message.subject or "",
            "body": message.body_text,
        }

        def _send_all() -> tuple[list[str], list[str]]:
            accepted, errors = [], []
            for token in tokens:
                try:
                    accepted.append(
                        messaging.send(messaging.Message(data=data, token=token))
                    )
                except Exception as exc:  # includes UnregisteredError for stale tokens
                    errors.append(f"{token[:12]}…: {exc}")
            return accepted, errors

        accepted, errors = await asyncio.to_thread(_send_all)
        if not accepted:
            raise ProviderSendError(f"FCM rejected all tokens: {'; '.join(errors)}")
        return SendResult(provider_message_id=accepted[0])
