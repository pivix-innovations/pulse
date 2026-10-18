from __future__ import annotations

from config.base import ConfigSection
from pydantic import PositiveInt, computed_field


class DatabaseSettings(ConfigSection):
    driver: str
    host: str
    port: PositiveInt
    username: str
    password: str
    db: str
    echo: bool
    pool_size: PositiveInt
    max_overflow: PositiveInt
    pool_timeout: PositiveInt
    pool_recycle: PositiveInt
    pool_pre_ping: bool
    ssl: bool = False   # True for managed Postgres (e.g. Neon)
    
    @computed_field
    @property
    def url(self) -> str:
        return (
            f"{self.driver}://"
            f"{self.username}:{self.password}@"
            f"{self.host}:{self.port}/"
            f"{self.db}"
            f"{'?ssl=require' if self.ssl else ''}"
        )
    

