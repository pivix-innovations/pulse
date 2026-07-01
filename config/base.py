from __future__ import annotations

from pydantic import BaseModel, ConfigDict
from pydantic_settings import BaseSettings, SettingsConfigDict


class ConfigSection(BaseModel):
    """Base class for all configuration sections"""

    model_config = ConfigDict(frozen=True, extra="forbid", validate_assignment=True)


class EnvironmentSettings(BaseSettings):
    """
    Base settings loader,
    This class in responsible only for loading values from the environments
    """

    model_config = SettingsConfigDict(
        env_prefix="PULSE_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        frozen=True,
    )
