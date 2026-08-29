from __future__ import annotations

from typing import Literal
from config.base import ConfigSection


class PushSettings(ConfigSection):
    driver: Literal["console", "fcm"] = "console"
    # Path to a Firebase service-account JSON. When unset, the FCM driver
    # falls back to GOOGLE_APPLICATION_CREDENTIALS.
    fcm_credentials_path: str | None = None
