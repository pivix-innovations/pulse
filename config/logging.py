from __future__ import annotations

from typing import Literal

from pydantic import ConfigDict, Field

from config.base import ConfigSection


class LoggingSettings(ConfigSection):
    model_config = ConfigDict(
        frozen=True, extra="forbid", validate_assignment=True, populate_by_name=True
    )

    level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    # Env/config key stays `json`; the attribute is renamed so it doesn't
    # shadow pydantic's BaseModel.json().
    json_format: bool = Field(default=True, alias="json")
    color: bool = False
    include_timestamp: bool = True
