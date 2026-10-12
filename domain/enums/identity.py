from __future__ import annotations

from enum import Enum


class TenantRole(str, Enum):
    """What a user may do inside one tenant."""

    ADMIN = "admin"    # manage members, API keys, and all tenant config
    MEMBER = "member"  # operate the console: send events, inspect, configure
