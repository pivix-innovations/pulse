from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from domain.value_objects.ids import TenantId


@dataclass(frozen=True, slots=True)
class Limits:
    rate_per_second: float
    burst: int
    daily_quota: int | None = None  # None = unlimited


@dataclass(frozen=True, slots=True)
class Decision:
    allowed: bool
    reason: Literal["rate", "quota"] | None = None
    retry_after_seconds: float = 0.0
    tokens_remaining: float = 0.0
    quota_remaining: int | None = None  # None = unlimited


class RateLimiter(Protocol):
    """Shared (cross-process) per-tenant ingress limiter."""

    async def acquire(self, tenant_id: TenantId, limits: Limits) -> Decision:
        """Spend one token (and one unit of the daily quota), or say why not."""
        ...

    async def refund_quota(self, tenant_id: TenantId) -> None:
        """Give back the quota unit of a request that turned out not to count."""
        ...

    async def used_today(self, tenant_id: TenantId) -> int: ...
