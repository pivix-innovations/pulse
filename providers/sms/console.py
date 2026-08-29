from __future__ import annotations

import logging
import uuid

from providers.sms.base import SmsProvider
from services.ports.providers import OutboundMessage, SendResult

logger = logging.getLogger(__name__)


class ConsoleSmsProvider(SmsProvider):
    """Logs the SMS instead of sending it; for local development."""

    name = "console"

    async def send(self, message: OutboundMessage) -> SendResult:
        message_id = f"console-{uuid.uuid4()}"
        logger.info("[sms/console] to=%s id=%s body=%r", message.to, message_id, message.body_text)
        return SendResult(provider_message_id=message_id)
