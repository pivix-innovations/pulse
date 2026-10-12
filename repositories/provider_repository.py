from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import ProviderModel
from domain.entities.provider import Provider
from domain.enums import ChannelType
from domain.value_objects.ids import ProviderId, TenantId


def _to_entity(row: ProviderModel) -> Provider:
    return Provider(
        id=ProviderId(row.id),
        tenant_id=TenantId(row.tenant_id),
        channel=row.channel,
        name=row.name,
        config=dict(row.config),
        priority=row.priority,
        is_active=row.is_active,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class PostgresProviderRepository:
    """Implements services.ports.repositories.ProviderRepository."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, provider: Provider) -> None:
        self._session.add(
            ProviderModel(
                id=provider.id.value,
                tenant_id=provider.tenant_id.value,
                channel=provider.channel,
                name=provider.name,
                config=provider.config,
                priority=provider.priority,
                is_active=provider.is_active,
            )
        )
        await self._session.flush()

    async def get(self, tenant_id: TenantId, provider_id: ProviderId) -> Provider | None:
        stmt = select(ProviderModel).where(
            ProviderModel.tenant_id == tenant_id.value,
            ProviderModel.id == provider_id.value,
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return _to_entity(row) if row else None

    async def list(self, tenant_id: TenantId) -> list[Provider]:
        stmt = (
            select(ProviderModel)
            .where(ProviderModel.tenant_id == tenant_id.value)
            .order_by(ProviderModel.priority.desc(), ProviderModel.created_at)
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_to_entity(row) for row in rows]

    async def get_active_by_channel(
        self, tenant_id: TenantId, channel: ChannelType
    ) -> Provider | None:
        stmt = (
            select(ProviderModel)
            .where(
                ProviderModel.tenant_id == tenant_id.value,
                ProviderModel.channel == channel,
                ProviderModel.is_active.is_(True),
            )
            .order_by(ProviderModel.priority.desc(), ProviderModel.created_at)
            .limit(1)
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return _to_entity(row) if row else None

    async def update(self, provider: Provider) -> None:
        row = await self._session.get(ProviderModel, provider.id.value)
        if row is None or row.tenant_id != provider.tenant_id.value:
            raise LookupError(f"Provider {provider.id} not found for tenant")
        row.channel = provider.channel
        row.name = provider.name
        row.config = provider.config
        row.priority = provider.priority
        row.is_active = provider.is_active
        await self._session.flush()

    async def delete(self, tenant_id: TenantId, provider_id: ProviderId) -> bool:
        row = await self._session.get(ProviderModel, provider_id.value)
        if row is None or row.tenant_id != tenant_id.value:
            return False
        await self._session.delete(row)
        await self._session.flush()
        return True
