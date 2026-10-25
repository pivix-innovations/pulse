from __future__ import annotations

from domain.value_objects.ids import RecipientId, TenantId
from providers.in_app.base import InAppProvider
from services.ports.providers import OutboundMessage, SendResult
from sse.broadcaster import SseBroadcaster


class SseInAppProvider(InAppProvider):
    """
    Live half of in-app delivery: pushes the message (already stored in the
    recipient's inbox) to any connected SSE clients. An offline recipient is
    not a failure -- the message waits in the inbox. `sent` means it is stored;
    reading it (or the client's ack) makes it `delivered`, and if that never
    happens the reconciliation sweep escalates to the workflow's fallback
    channel.
    """

    name = "sse"
    confirms_delivery = True

    def __init__(self, broadcaster: SseBroadcaster) -> None:
        self._broadcaster = broadcaster

    async def send(self, message: OutboundMessage) -> SendResult:
        tenant_id = TenantId.from_str(message.metadata["tenant_id"])
        recipient_id = RecipientId.from_str(message.metadata["recipient_id"])
        receivers = await self._broadcaster.publish(
            tenant_id,
            recipient_id,
            {
                "id": message.metadata.get("attempt_id"),
                "inbox_id": message.metadata.get("inbox_id"),
                "notification_id": message.metadata.get("notification_id"),
                "title": message.subject,
                "body": message.body_text,
            },
        )
        return SendResult(provider_message_id=f"sse-receivers-{receivers}")
