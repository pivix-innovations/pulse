from __future__ import annotations

from typing import Literal
from config.base import ConfigSection
from pydantic import PositiveInt


class EmailSettings(ConfigSection):
    driver: Literal["console", "smtp"] = "console"
    from_address: str = "pulse@localhost"
    smtp_host: str = "localhost"
    smtp_port: PositiveInt = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_use_tls: bool = True
