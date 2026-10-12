from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import EventModel
from domain.entities.event import Event
from domain.value_objects.ids import EventId, TenantId


def _to_entity(row: EventModel) -> Event:
    return Event(
        id=EventId(row.id),
        tenant_id=TenantId(row.tenant_id),
        event_type=row.event_type,
        source=row.source,
        payload=dict(row.payload),
        idempotency_key=row.idempotency_key,
        occurred_at=row.occurred_at,
        received_at=row.received_at,
    )


class PostgresEventRepository:
    """Implements services.ports.repositories.EventRepository."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, event: Event) -> None:
        self._session.add(
            EventModel(
                id=event.id.value,
                tenant_id=event.tenant_id.value,
                event_type=event.event_type,
                source=event.source,
                payload=event.payload,
                idempotency_key=event.idempotency_key,
                occurred_at=event.occurred_at,
                received_at=event.received_at,
            )
        )
        await self._session.flush()

    async def get_by_idempotency_key(self, tenant_id: TenantId, key: str) -> Event | None:
        stmt = select(EventModel).where(
            EventModel.tenant_id == tenant_id.value,
            EventModel.idempotency_key == key,
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return _to_entity(row) if row else None

    async def list_for_tenant(
        self, tenant_id: TenantId, *, limit: int = 50, offset: int = 0
    ) -> tuple[list[Event], int]:
        total = (
            await self._session.execute(
                select(func.count()).select_from(EventModel).where(EventModel.tenant_id == tenant_id.value)
            )
        ).scalar_one()
        rows = (
            await self._session.execute(
                select(EventModel)
                .where(EventModel.tenant_id == tenant_id.value)
                .order_by(EventModel.received_at.desc())
                .limit(limit)
                .offset(offset)
            )
        ).scalars().all()
        return [_to_entity(r) for r in rows], total
