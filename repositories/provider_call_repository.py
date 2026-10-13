from __future__ import annotations

from datetime import datetime
from typing import Sequence

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import ProviderCallModel
from domain.entities.provider_call import ProviderCall
from domain.value_objects.ids import ProviderId, TenantId
from services.ports.repositories import ProviderCallStats

RECENT = 5  # how many latest calls feed the "is it down right now" signal


class PostgresProviderCallRepository:
    """Implements services.ports.repositories.ProviderCallRepository."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_many(self, calls: Sequence[ProviderCall]) -> None:
        self._session.add_all(
            ProviderCallModel(
                tenant_id=c.tenant_id.value,
                provider_id=c.provider_id.value,
                attempt_id=c.attempt_id.value,
                channel=c.channel,
                outcome=c.outcome,
                latency_ms=c.latency_ms,
                error=c.error,
            )
            for c in calls
        )
        await self._session.flush()

    async def stats(self, tenant_id: TenantId, since: datetime) -> dict[ProviderId, ProviderCallStats]:
        m = ProviderCallModel
        scope = and_(m.tenant_id == tenant_id.value, m.created_at >= since)
        counted = m.outcome != "recipient_error"  # recipient errors never count against a provider

        agg = await self._session.execute(
            select(
                m.provider_id,
                func.count().filter(counted),
                func.count().filter(m.outcome == "provider_error"),
                func.avg(m.latency_ms).filter(m.outcome == "ok"),
                func.percentile_cont(0.95).within_group(m.latency_ms).filter(m.outcome == "ok"),
                func.max(m.created_at).filter(m.outcome == "ok"),
                func.max(m.created_at).filter(m.outcome == "provider_error"),
            )
            .where(scope)
            .group_by(m.provider_id)
        )
        stats = {
            ProviderId(pid): ProviderCallStats(
                calls=calls or 0,
                failures=failures or 0,
                avg_latency_ms=float(avg) if avg is not None else None,
                p95_latency_ms=float(p95) if p95 is not None else None,
                last_success_at=last_ok,
                last_failure_at=last_fail,
            )
            for pid, calls, failures, avg, p95, last_ok, last_fail in agg.all()
        }

        last_errors = await self._session.execute(
            select(m.provider_id, m.error)
            .where(scope, m.outcome == "provider_error")
            .distinct(m.provider_id)
            .order_by(m.provider_id, m.created_at.desc())
        )
        for pid, error in last_errors.all():
            stats[ProviderId(pid)].last_error = error

        ranked = (
            select(
                m.provider_id.label("pid"),
                m.outcome.label("outcome"),
                func.row_number()
                .over(partition_by=m.provider_id, order_by=m.created_at.desc())
                .label("rn"),
            )
            .where(scope, counted)
            .subquery()
        )
        recent = await self._session.execute(
            select(ranked.c.pid, ranked.c.outcome).where(ranked.c.rn <= RECENT).order_by(ranked.c.pid, ranked.c.rn)
        )
        for pid, outcome in recent.all():
            stats[ProviderId(pid)].recent.append(outcome)
        return stats

    async def prune(self, before: datetime) -> int:
        from sqlalchemy import delete

        result = await self._session.execute(delete(ProviderCallModel).where(ProviderCallModel.created_at < before))
        await self._session.flush()
        return result.rowcount or 0
