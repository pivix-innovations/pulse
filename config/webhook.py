from __future__ import annotations

from typing import Dict
from config.base import ConfigSection


class WebhookSettings(ConfigSection):
    # Maps a provider slug (URL path segment) to its HMAC signing secret.
    # e.g. PULSE_WEBHOOK__SECRETS={"console": "dev-webhook-secret"}
    secrets: Dict[str, str] = {}
    # Trust SNS certificates / subscribe URLs on localhost. Only for testing
    # against a local emulator such as LocalStack; never enable in production.
    allow_insecure_sns: bool = False
