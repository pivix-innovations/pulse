from __future__ import annotations

from config.email import EmailSettings
from providers.email.base import EmailProvider
from providers.email.console import ConsoleEmailProvider
from providers.email.smtp import SmtpEmailProvider


def build_email_provider(settings: EmailSettings) -> EmailProvider:
    if settings.driver == "smtp":
        return SmtpEmailProvider(settings)
    return ConsoleEmailProvider()
