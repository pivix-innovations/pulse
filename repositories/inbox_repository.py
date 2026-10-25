from __future__ import annotations

from datetime import datetime
from typing import Sequence

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import InboxMessageModel
from domain.entities.inbox import InboxMessage
from domain.value_objects.ids import (
    DeliveryAttemptId,
    InboxMessageId,
    NotificationId,
    RecipientId,
    TenantId,
)

M = InboxMessageModel


def _to_entity(row: InboxMessageModel) -> InboxMessage:
    return InboxMessage(
        id=InboxMessageId(row.id),
        tenant_id=TenantId(row.tenant_id),
        recipient_id=RecipientId(row.recipient_id),
        notification_id=NotificationId(row.notification_id),
        attempt_id=DeliveryAttemptId(row.attempt_id),
        title=row.title,
        body=row.body,
        read_at=row.read_at,
        archived_at=row.archived_at,
        created_at=row.created_at,
    )


def _scope(tenant_id: TenantId, recipient_id: RecipientId):
    return (M.tenant_id == tenant_id.value, M.recipient_id == recipient_id.value)


class PostgresInboxRepository:
    """Implements services.ports.repositories.InboxRepository."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, message: InboxMessage) -> InboxMessage:
        # Idempotent per attempt: a redelivered job must not duplicate the message.
        await self._session.execute(
            pg_insert(M)
            .values(
                id=message.id.value,
                tenant_id=message.tenant_id.value,
                recipient_id=message.recipient_id.value,
                notification_id=message.notification_id.value,
                attempt_id=message.attempt_id.value,
                title=message.title,
                body=message.body,
            )
            .on_conflict_do_nothing(constraint="uq_inbox_messages_attempt_id")
        )
        row = (
            await self._session.execute(select(M).where(M.attempt_id == message.attempt_id.value))
        ).scalar_one()
        return _to_entity(row)

    async def get(
        self, tenant_id: TenantId, recipient_id: RecipientId, message_id: InboxMessageId
    ) -> InboxMessage | None:
        row = (
            await self._session.execute(
                select(M).where(*_scope(tenant_id, recipient_id), M.id == message_id.value)
            )
        ).scalar_one_or_none()
        return _to_entity(row) if row else None

    async def list(
        self,
        tenant_id: TenantId,
        recipient_id: RecipientId,
        *,
        state: str = "active",
        limit: int = 50,
        before: tuple[datetime, InboxMessageId] | None = None,
    ) -> list[InboxMessage]:
        stmt = select(M).where(*_scope(tenant_id, recipient_id))
        if state == "active":
            stmt = stmt.where(M.archived_at.is_(None))
        elif state == "unread":
            stmt = stmt.where(M.archived_at.is_(None), M.read_at.is_(None))
        elif state == "read":
            stmt = stmt.where(M.archived_at.is_(None), M.read_at.is_not(None))
        elif state == "archived":
            stmt = stmt.where(M.archived_at.is_not(None))
        if before is not None:
            created, mid = before
            stmt = stmt.where(
                or_(M.created_at < created, and_(M.created_at == created, M.id < mid.value))
            )
        stmt = stmt.order_by(M.created_at.desc(), M.id.desc()).limit(limit)
        return [_to_entity(r) for r in (await self._session.execute(stmt)).scalars().all()]

    async def unread_count(self, tenant_id: TenantId, recipient_id: RecipientId) -> int:
        return (
            await self._session.execute(
                select(func.count()).where(
                    *_scope(tenant_id, recipient_id), M.read_at.is_(None), M.archived_at.is_(None)
                )
            )
        ).scalar_one()

    async def mark_read(
        self,
        tenant_id: TenantId,
        recipient_id: RecipientId,
        ids: Sequence[InboxMessageId] | None,
        at: datetime,
    ) -> list[InboxMessage]:
        """Mark unread messages read (all of them when `ids` is None); returns the ones that changed."""
        stmt = update(M).where(*_scope(tenant_id, recipient_id), M.read_at.is_(None))
        if ids is not None:
            stmt = stmt.where(M.id.in_([i.value for i in ids]))
        else:
            stmt = stmt.where(M.archived_at.is_(None))
        rows = (await self._session.execute(stmt.values(read_at=at).returning(M))).scalars().all()
        return [_to_entity(r) for r in rows]

    async def mark_unread(
        self, tenant_id: TenantId, recipient_id: RecipientId, message_id: InboxMessageId
    ) -> bool:
        res = await self._session.execute(
            update(M)
            .where(*_scope(tenant_id, recipient_id), M.id == message_id.value)
            .values(read_at=None)
        )
        return res.rowcount > 0

    async def set_archived(
        self,
        tenant_id: TenantId,
        recipient_id: RecipientId,
        message_id: InboxMessageId,
        at: datetime | None,
    ) -> bool:
        res = await self._session.execute(
            update(M)
            .where(*_scope(tenant_id, recipient_id), M.id == message_id.value)
            .values(archived_at=at)
        )
        return res.rowcount > 0

    async def prune(self, created_before: datetime, archived_before: datetime) -> int:
        """Drop old messages: anything created before `created_before`, or archived before `archived_before`."""
        from sqlalchemy import delete

        res = await self._session.execute(
            delete(M).where(or_(M.created_at < created_before, M.archived_at < archived_before))
        )
        return res.rowcount
