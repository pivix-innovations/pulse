from __future__ import annotations

from config.email import EmailSettings
from providers.email.base import EmailProvider
from providers.email.console import ConsoleEmailProvider
from providers.email.resend import ResendEmailProvider
from providers.email.smtp import SmtpEmailProvider


def build_email_provider(settings: EmailSettings) -> EmailProvider:
    if settings.driver == "smtp":
        return SmtpEmailProvider(settings)
    if settings.driver == "resend":
        if not settings.resend_api_key:
            raise ValueError("PULSE_EMAIL__RESEND_API_KEY is required when PULSE_EMAIL__DRIVER=resend")
        return ResendEmailProvider(api_key=settings.resend_api_key, from_address=settings.from_address)
    return ConsoleEmailProvider()
