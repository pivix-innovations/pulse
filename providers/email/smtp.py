from __future__ import annotations

from email.message import EmailMessage

import aiosmtplib

from config.email import EmailSettings
from providers.email.base import EmailProvider
from services.ports.providers import OutboundMessage, ProviderSendError, SendResult


class SmtpEmailProvider(EmailProvider):
    name = "smtp"
    confirms_delivery = False

    def __init__(self, settings: EmailSettings) -> None:
        self._settings = settings

    async def send(self, message: OutboundMessage) -> SendResult:
        email = EmailMessage()
        email["From"] = self._settings.from_address
        email["To"] = message.to
        email["Subject"] = message.subject or ""
        if message.idempotency_key:
            email["X-Pulse-Idempotency-Key"] = message.idempotency_key
        email.set_content(message.body_text)
        if message.body_html:
            email.add_alternative(message.body_html, subtype="html")

        try:
            _, response = await aiosmtplib.send(
                email,
                hostname=self._settings.smtp_host,
                port=self._settings.smtp_port,
                username=self._settings.smtp_username,
                password=self._settings.smtp_password,
                start_tls=self._settings.smtp_use_tls,
            )
        except aiosmtplib.SMTPException as exc:
            raise ProviderSendError(f"SMTP send failed: {exc}") from exc
        return SendResult(provider_message_id=email["Message-ID"] or response)
