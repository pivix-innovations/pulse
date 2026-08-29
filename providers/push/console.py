from __future__ import annotations

import logging
import uuid

from providers.push.base import PushProvider
from services.ports.providers import OutboundMessage, SendResult

logger = logging.getLogger(__name__)


class ConsolePushProvider(PushProvider):
    """Logs the push payload instead of sending it; for local development."""

    name = "console"

    async def send(self, message: OutboundMessage) -> SendResult:
        message_id = f"console-{uuid.uuid4()}"
        logger.info(
            "[push/console] token=%s title=%r id=%s body=%r",
            message.to,
            message.subject,
            message_id,
            message.body_text,
        )
        return SendResult(provider_message_id=message_id)
