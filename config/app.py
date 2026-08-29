from __future__ import annotations

from typing import Literal, List
from config.base import ConfigSection


class AppSettings(ConfigSection):
    name: str = "Pulse"
    version: str = "0.0.1"
    environment: Literal["dev", "test", "stage", "prod"] = "dev"
    debug: bool = False
    timezone: str = "UTC"
    allowed_origins: List[str] = ["*"]
