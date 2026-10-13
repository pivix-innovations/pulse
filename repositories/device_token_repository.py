from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import DeviceTokenModel
from domain.entities.device import DeviceToken
from domain.value_objects.ids import DeviceTokenId, RecipientId, TenantId


def _to_entity(row: DeviceTokenModel) -> DeviceToken:
    return DeviceToken(
        id=DeviceTokenId(row.id),
        tenant_id=TenantId(row.tenant_id),
        recipient_id=RecipientId(row.recipient_id),
        platform=row.platform,
        token=row.token,
        last_seen_at=row.last_seen_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class PostgresDeviceTokenRepository:
    """Implements services.ports.repositories.DeviceTokenRepository."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert(self, token: DeviceToken) -> DeviceToken:
        stmt = select(DeviceTokenModel).where(
            DeviceTokenModel.tenant_id == token.tenant_id.value,
            DeviceTokenModel.token == token.token,
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        now = datetime.now(timezone.utc)
        if row is not None:
            # Token re-registered, possibly by a different recipient/platform.
            row.recipient_id = token.recipient_id.value
            row.platform = token.platform
            row.last_seen_at = now
            await self._session.flush()
            # flush expires server-side updated_at; refresh so mapping stays sync
            await self._session.refresh(row)
            return _to_entity(row)

        self._session.add(
            DeviceTokenModel(
                id=token.id.value,
                tenant_id=token.tenant_id.value,
                recipient_id=token.recipient_id.value,
                platform=token.platform,
                token=token.token,
                last_seen_at=now,
            )
        )
        await self._session.flush()
        token.last_seen_at = now
        return token

    async def list_for_recipient(
        self, tenant_id: TenantId, recipient_id: RecipientId
    ) -> list[DeviceToken]:
        stmt = (
            select(DeviceTokenModel)
            .where(
                DeviceTokenModel.tenant_id == tenant_id.value,
                DeviceTokenModel.recipient_id == recipient_id.value,
            )
            .order_by(DeviceTokenModel.last_seen_at.desc())
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_to_entity(row) for row in rows]

    async def delete_tokens(self, tenant_id: TenantId, tokens: list[str]) -> int:
        if not tokens:
            return 0
        result = await self._session.execute(
            delete(DeviceTokenModel).where(
                DeviceTokenModel.tenant_id == tenant_id.value,
                DeviceTokenModel.token.in_(tokens),
            )
        )
        await self._session.flush()
        return result.rowcount or 0
