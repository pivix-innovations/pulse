from __future__ import annotations

import logging
import uuid

from providers.email.base import EmailProvider
from services.ports.providers import OutboundMessage, SendResult

logger = logging.getLogger(__name__)


class ConsoleEmailProvider(EmailProvider):
    """Logs the email instead of sending it; for local development."""

    name = "console"

    async def send(self, message: OutboundMessage) -> SendResult:
        message_id = f"console-{uuid.uuid4()}"
        logger.info(
            "[email/console] to=%s subject=%r id=%s\n%s",
            message.to,
            message.subject,
            message_id,
            message.body_text,
        )
        return SendResult(provider_message_id=message_id)
