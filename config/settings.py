from __future__ import annotations

from config.base import EnvironmentSettings
from config.app import AppSettings
from config.api import ApiSettings
from config.logging import LoggingSettings
from pydantic import PositiveInt
from typing import List


class Settings(EnvironmentSettings):
    app_name: str = "Pulse"
    app_version: str = "0.0.1"
    app_environment: str = "dev"
    app_debug: bool = True
    app_timezone: str = "UTC"
    app_allowed_origins: List[str] = ["*"]

    api_host: str = "0.0.0.0"
    api_port: PositiveInt = 9001
    api_docs_url: str | None = "/docs"
    api_redoc_url: str | None = "/redoc"
    api_openapi_url: str = "/openapi.json"

    logging_level: str = "DEBUG"
    logging_json: bool = True
    logging_color: bool = False
    logging_include_timestamp: bool = True

    @property
    def app(self) -> AppSettings:
        return AppSettings(
            name=self.app_name,
            version=self.app_name,
            environment=self.app_environment,
            debug=self.app_debug,
            timezone=self.app_timezone,
            allowed_origins=self.app_allowed_origins,
        )

    @property
    def api(self) -> ApiSettings:
        return ApiSettings(
            host=self.api_host,
            port=self.api_port,
            docs_url=self.api_docs_url,
            redoc_url=self.api_redoc_url,
            openapi_url=self.api_openapi_url,
        )

    @property
    def logging(self) -> LoggingSettings:
        return LoggingSettings(
            level=self.logging_level,
            json=self.logging_json,
            color=self.logging_color,
            include_timestamp=self.logging_include_timestamp,
        )
