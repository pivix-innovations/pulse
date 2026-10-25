from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import SuppressionModel
from domain.entities.suppression import Suppression, normalize_address
from domain.enums import ChannelType
from domain.value_objects.ids import SuppressionId, TenantId


def _to_entity(row: SuppressionModel) -> Suppression:
    return Suppression(
        id=SuppressionId(row.id),
        tenant_id=TenantId(row.tenant_id),
        channel=row.channel,
        address=row.address,
        reason=row.reason,
        detail=row.detail,
        created_at=row.created_at,
    )


class PostgresSuppressionRepository:
    """Implements services.ports.repositories.SuppressionRepository."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, suppression: Suppression) -> bool:
        stmt = (
            pg_insert(SuppressionModel)
            .values(
                id=suppression.id.value,
                tenant_id=suppression.tenant_id.value,
                channel=suppression.channel,
                address=normalize_address(suppression.channel, suppression.address),
                reason=suppression.reason,
                detail=suppression.detail,
            )
            .on_conflict_do_nothing(constraint="uq_suppressions_tenant_channel_address")
            .returning(SuppressionModel.id)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none() is not None

    async def is_suppressed(self, tenant_id: TenantId, channel: ChannelType, address: str) -> bool:
        stmt = select(SuppressionModel.id).where(
            SuppressionModel.tenant_id == tenant_id.value,
            SuppressionModel.channel == channel,
            SuppressionModel.address == normalize_address(channel, address),
        )
        return (await self._session.execute(stmt)).first() is not None

    async def list(self, tenant_id: TenantId, *, limit: int = 50, offset: int = 0) -> list[Suppression]:
        stmt = (
            select(SuppressionModel)
            .where(SuppressionModel.tenant_id == tenant_id.value)
            .order_by(SuppressionModel.created_at.desc(), SuppressionModel.id)
            .limit(limit)
            .offset(offset)
        )
        return [_to_entity(r) for r in (await self._session.execute(stmt)).scalars()]

    async def delete(self, tenant_id: TenantId, suppression_id: SuppressionId) -> bool:
        result = await self._session.execute(
            delete(SuppressionModel).where(
                SuppressionModel.tenant_id == tenant_id.value,
                SuppressionModel.id == suppression_id.value,
            )
        )
        return result.rowcount > 0
