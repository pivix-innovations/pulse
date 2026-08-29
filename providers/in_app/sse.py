from __future__ import annotations

from domain.value_objects.ids import RecipientId, TenantId
from providers.in_app.base import InAppProvider
from services.ports.providers import OutboundMessage, ProviderSendError, SendResult
from sse.broadcaster import SseBroadcaster


class SseInAppProvider(InAppProvider):
    """
    Delivers in-app notifications over the SSE pub/sub fabric. `sent` here
    means a live connection received the publish; the client's ack makes it
    `delivered`. No live connection is a non-retryable failure -- the
    reconciliation sweep escalates it to the workflow's fallback channel.
    """

    name = "sse"

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
                "notification_id": message.metadata.get("notification_id"),
                "title": message.subject,
                "body": message.body_text,
            },
        )
        if receivers == 0:
            raise ProviderSendError("No live SSE connection for recipient", retryable=False)
        return SendResult(provider_message_id=f"sse-receivers-{receivers}")
