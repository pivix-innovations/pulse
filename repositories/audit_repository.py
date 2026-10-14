from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models.audit import AuditLogModel
from domain.entities.audit import AuditEntry
from domain.value_objects.ids import AuditEntryId, TenantId


def _to_entity(r: AuditLogModel) -> AuditEntry:
    return AuditEntry(
        id=AuditEntryId(r.id), tenant_id=TenantId(r.tenant_id), actor_type=r.actor_type, actor_id=r.actor_id,
        actor_label=r.actor_label, action=r.action, resource_type=r.resource_type, resource_id=r.resource_id,
        detail=r.detail or {}, created_at=r.created_at,
    )


class PostgresAuditRepository:
    """Implements services.ports.repositories.AuditRepository."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, entry: AuditEntry) -> None:
        self._session.add(AuditLogModel(
            id=entry.id.value, tenant_id=entry.tenant_id.value, actor_type=entry.actor_type,
            actor_id=entry.actor_id, actor_label=entry.actor_label, action=entry.action,
            resource_type=entry.resource_type, resource_id=entry.resource_id, detail=entry.detail,
        ))
        await self._session.flush()

    async def list(
        self, tenant_id: TenantId, *, resource_type: str | None = None, resource_id: str | None = None,
        limit: int = 50, offset: int = 0,
    ) -> list[AuditEntry]:
        stmt = select(AuditLogModel).where(AuditLogModel.tenant_id == tenant_id.value)
        if resource_type:
            stmt = stmt.where(AuditLogModel.resource_type == resource_type)
        if resource_id:
            stmt = stmt.where(AuditLogModel.resource_id == resource_id)
        stmt = stmt.order_by(AuditLogModel.created_at.desc(), AuditLogModel.id).limit(limit).offset(offset)
        return [_to_entity(r) for r in (await self._session.execute(stmt)).scalars()]
