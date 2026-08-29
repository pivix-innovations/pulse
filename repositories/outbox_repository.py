from __future__ import annotations

from datetime import datetime, timezone
from typing import Sequence

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import OutboxEntryModel
from domain.entities.outbox import OutboxEntry
from domain.enums import OutboxStatus
from domain.value_objects.ids import OutboxEntryId, TenantId


def _to_entity(row: OutboxEntryModel) -> OutboxEntry:
    return OutboxEntry(
        id=OutboxEntryId(row.id),
        tenant_id=TenantId(row.tenant_id),
        aggregate_type=row.aggregate_type,
        aggregate_id=row.aggregate_id,
        topic=row.topic,
        payload=dict(row.payload),
        status=row.status,
        created_at=row.created_at,
        published_at=row.published_at,
    )


class PostgresOutboxRepository:
    """Implements services.ports.repositories.OutboxRepository."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_many(self, entries: Sequence[OutboxEntry]) -> None:
        self._session.add_all(
            OutboxEntryModel(
                id=e.id.value,
                tenant_id=e.tenant_id.value,
                aggregate_type=e.aggregate_type,
                aggregate_id=e.aggregate_id,
                topic=e.topic,
                payload=e.payload,
                status=e.status,
            )
            for e in entries
        )
        await self._session.flush()

    async def claim_pending(self, limit: int) -> list[OutboxEntry]:
        # SKIP LOCKED lets multiple relay instances poll without contention.
        stmt = (
            select(OutboxEntryModel)
            .where(OutboxEntryModel.status == OutboxStatus.PENDING)
            .order_by(OutboxEntryModel.created_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_to_entity(row) for row in rows]

    async def mark_published(self, entry_ids: Sequence[OutboxEntryId]) -> None:
        if not entry_ids:
            return
        stmt = (
            update(OutboxEntryModel)
            .where(OutboxEntryModel.id.in_([e.value for e in entry_ids]))
            .values(status=OutboxStatus.PUBLISHED, published_at=datetime.now(timezone.utc))
        )
        await self._session.execute(stmt)
        await self._session.flush()
