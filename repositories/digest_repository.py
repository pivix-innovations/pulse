from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import exists, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import DigestBatchModel, DigestItemModel
from domain.entities.digest import DigestBatch, DigestItem, DigestStatus
from domain.value_objects.ids import (
    DigestBatchId,
    DigestItemId,
    NotificationId,
    RecipientId,
    TenantId,
    WorkflowId,
)

B, It = DigestBatchModel, DigestItemModel


def _batch(row: DigestBatchModel) -> DigestBatch:
    return DigestBatch(
        id=DigestBatchId(row.id), tenant_id=TenantId(row.tenant_id), workflow_id=WorkflowId(row.workflow_id),
        recipient_id=RecipientId(row.recipient_id), digest_key=row.digest_key,
        anchor_notification_id=NotificationId(row.anchor_notification_id), flush_at=row.flush_at,
        status=DigestStatus(row.status), item_count=row.item_count, flushed_at=row.flushed_at,
        created_at=row.created_at,
    )


def _item(row: DigestItemModel) -> DigestItem:
    return DigestItem(
        id=DigestItemId(row.id), tenant_id=TenantId(row.tenant_id), batch_id=DigestBatchId(row.batch_id),
        notification_id=NotificationId(row.notification_id), payload=dict(row.payload), created_at=row.created_at,
    )


class PostgresDigestRepository:
    """Implements services.ports.repositories.DigestRepository."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_item(
        self,
        *,
        tenant_id: TenantId,
        workflow_id: WorkflowId,
        recipient_id: RecipientId,
        digest_key: str,
        notification_id: NotificationId,
        payload: dict[str, Any],
        now: datetime,
        window_seconds: int,
        max_items: int,
    ) -> DigestBatch:
        for _ in range(3):
            # Join the open batch, or open one (the first event is the anchor).
            await self._session.execute(
                pg_insert(B)
                .values(
                    id=uuid.uuid4(), tenant_id=tenant_id.value, workflow_id=workflow_id.value,
                    recipient_id=recipient_id.value, digest_key=digest_key,
                    anchor_notification_id=notification_id.value, status="open", item_count=0,
                    flush_at=now + timedelta(seconds=window_seconds),
                )
                .on_conflict_do_nothing(
                    index_elements=[B.workflow_id, B.recipient_id, B.digest_key],
                    # Literal, not a bind parameter: Postgres only matches a partial index to a constant predicate.
                    index_where=text("status = 'open'"),
                )
            )
            # Locking the batch serialises us with a flush in progress.
            row = (
                await self._session.execute(
                    select(B)
                    .where(
                        B.workflow_id == workflow_id.value, B.recipient_id == recipient_id.value,
                        B.digest_key == digest_key, B.status == "open",
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if row is not None:
                break
        else:  # pragma: no cover - needs three flushes racing one ingest
            raise RuntimeError("could not open a digest batch")

        self._session.add(
            It(id=uuid.uuid4(), tenant_id=tenant_id.value, batch_id=row.id,
              notification_id=notification_id.value, payload=payload)
        )
        row.item_count += 1
        if row.item_count >= max_items:
            row.flush_at = min(row.flush_at, now)   # full: send on the next pass
        await self._session.flush()
        await self._session.refresh(row)
        return _batch(row)

    async def claim_due(self, now: datetime, limit: int) -> list[DigestBatch]:
        rows = (
            await self._session.execute(
                select(B).where(B.status == "open", B.flush_at <= now).order_by(B.flush_at).limit(limit)
                .with_for_update(skip_locked=True)
            )
        ).scalars().all()
        return [_batch(r) for r in rows]

    async def list_items(self, batch_id: DigestBatchId) -> list[DigestItem]:
        rows = (
            await self._session.execute(
                select(It).where(It.batch_id == batch_id.value).order_by(It.created_at, It.id)
            )
        ).scalars().all()
        return [_item(r) for r in rows]

    async def mark_flushed(self, batch: DigestBatch, at: datetime) -> None:
        batch.status, batch.flushed_at = DigestStatus.FLUSHED, at
        await self._session.execute(
            update(B).where(B.id == batch.id.value).values(status="flushed", flushed_at=at)
        )

    async def has_open_for_notification(self, notification_id: NotificationId) -> bool:
        return (
            await self._session.execute(
                select(
                    exists().where(
                        It.notification_id == notification_id.value, It.batch_id == B.id, B.status == "open"
                    )
                )
            )
        ).scalar_one()

    async def get_for_notification(
        self, tenant_id: TenantId, notification_id: NotificationId
    ) -> DigestBatch | None:
        row = (
            await self._session.execute(
                select(B)
                .join(It, It.batch_id == B.id)
                .where(B.tenant_id == tenant_id.value, It.notification_id == notification_id.value)
                .limit(1)
            )
        ).scalar_one_or_none()
        return _batch(row) if row else None
