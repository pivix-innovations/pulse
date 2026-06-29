from __future__ import annotations

from config.base import EnvironmentSettings
from config.app import AppSettings
from config.api import ApiSettings
from config.logging import LoggingSettings
from config.database import DatabaseSettings


class Settings(EnvironmentSettings):
    app: AppSettings = AppSettings()
    database: DatabaseSettings = DatabaseSettings()
    api: ApiSettings = ApiSettings()
    logging: LoggingSettings = LoggingSettings()
