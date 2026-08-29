from __future__ import annotations

from enum import Enum


class DevicePlatform(str, Enum):
    IOS = "ios"
    ANDROID = "android"
    WEB = "web"
