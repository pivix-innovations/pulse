from __future__ import annotations

from typing import Literal
from config.base import ConfigSection


class LoggingSettings(ConfigSection):
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    json: bool = True
    color: bool = False
    include_timestamp: bool = True
