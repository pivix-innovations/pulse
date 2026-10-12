from __future__ import annotations

from providers.sms.base import SmsProvider
from providers.sms.console import ConsoleSmsProvider


def build_sms_provider(driver: str = "console") -> SmsProvider:
    # Twilio/MSG91 drivers arrive in Phase 3; console keeps the pipeline
    # runnable locally without credentials.
    return ConsoleSmsProvider()
