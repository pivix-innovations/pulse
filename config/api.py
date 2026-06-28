from __future__ import annotations

from config.base import ConfigSection
from pydantic import  PositiveInt


class ApiSettings(ConfigSection):
    host: str = "0.0.0.0"
    port: PositiveInt = 9000
    docs_url: str | None = "/docs"
    redoc_url: str | None = "/redoc"
    openapi_url: str = "/openapi.json"
