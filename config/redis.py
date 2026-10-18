from __future__ import annotations

from config.base import ConfigSection
from pydantic import PositiveInt


class RedisSettings(ConfigSection):
    host: str = "localhost"
    port: PositiveInt = 6380
    db: int = 0
    password: str | None = None
    username: str | None = None
    ssl: bool = False   # True for managed Redis (e.g. Upstash, rediss://)
