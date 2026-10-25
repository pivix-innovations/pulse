from __future__ import annotations

import logging
import time

from config.ratelimit import RateLimitSettings
from domain.value_objects.ids import TenantId
from services.ports.rate_limiter import Decision, Limits, RateLimiter
from services.ports.uow import UnitOfWorkFactory

logger = logging.getLogger(__name__)

ALLOW = Decision(allowed=True)


class RateLimitService:
    """
    Resolves a tenant's effective limits (its overrides over the platform
    defaults, cached briefly) and applies them through the shared limiter.

    The limiter is an optimisation for protecting the pipeline, never a
    reason to drop traffic: if Redis is unavailable, requests are let through.
    """

    def __init__(
        self,
        limiter: RateLimiter,
        uow_factory: UnitOfWorkFactory,
        settings: RateLimitSettings,
    ) -> None:
        self._limiter = limiter
        self._uow_factory = uow_factory
        self._settings = settings
        self._cache: dict[TenantId, tuple[float, Limits]] = {}

    @property
    def enabled(self) -> bool:
        return self._settings.enabled

    async def limits_for(self, tenant_id: TenantId) -> Limits:
        cached = self._cache.get(tenant_id)
        now = time.monotonic()
        if cached and now - cached[0] < self._settings.limits_cache_seconds:
            return cached[1]
        async with self._uow_factory() as uow:
            tenant = await uow.tenants.get(tenant_id)
        s = self._settings
        limits = Limits(
            rate_per_second=(tenant.rate_limit_per_second if tenant and tenant.rate_limit_per_second else s.default_rate_per_second),
            burst=(tenant.rate_burst if tenant and tenant.rate_burst else s.default_burst),
            daily_quota=(tenant.daily_quota if tenant and tenant.daily_quota else s.default_daily_quota),
        )
        self._cache[tenant_id] = (now, limits)
        return limits

    def forget(self, tenant_id: TenantId) -> None:
        """Drop cached limits after an admin changed them."""
        self._cache.pop(tenant_id, None)

    async def check(self, tenant_id: TenantId) -> tuple[Decision, Limits | None]:
        if not self._settings.enabled:
            return ALLOW, None
        try:
            limits = await self.limits_for(tenant_id)
            return await self._limiter.acquire(tenant_id, limits), limits
        except Exception:
            logger.exception("Rate limiter unavailable; letting the request through")
            return ALLOW, None

    async def refund(self, tenant_id: TenantId) -> None:
        try:
            await self._limiter.refund_quota(tenant_id)
        except Exception:
            logger.exception("Quota refund failed")

    async def usage(self, tenant_id: TenantId) -> tuple[Limits, int]:
        return await self.limits_for(tenant_id), await self._limiter.used_today(tenant_id)
