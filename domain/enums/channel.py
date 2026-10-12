from __future__ import annotations

from enum import Enum


class ChannelType(str, Enum):
    EMAIL = "email"
    PUSH = "push"
    SMS = "sms"
    IN_APP = "in_app"
