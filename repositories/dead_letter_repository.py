from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import DeadLetterModel
from domain.entities.dead_letter import DeadLetter
from domain.value_objects.ids import (
    DeadLetterId,
    DeliveryAttemptId,
    NotificationId,
    TenantId,
)


def _to_entity(row: DeadLetterModel) -> DeadLetter:
    return DeadLetter(
        id=DeadLetterId(row.id),
        tenant_id=TenantId(row.tenant_id),
        attempt_id=DeliveryAttemptId(row.attempt_id),
        notification_id=NotificationId(row.notification_id),
        channel=row.channel,
        reason=row.reason,
        created_at=row.created_at,
    )


class PostgresDeadLetterRepository:
    """Implements services.ports.repositories.DeadLetterRepository."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, dead_letter: DeadLetter) -> None:
        self._session.add(
            DeadLetterModel(
                id=dead_letter.id.value,
                tenant_id=dead_letter.tenant_id.value,
                attempt_id=dead_letter.attempt_id.value,
                notification_id=dead_letter.notification_id.value,
                channel=dead_letter.channel,
                reason=dead_letter.reason,
            )
        )
        await self._session.flush()

    async def get(self, tenant_id: TenantId, dead_letter_id: DeadLetterId) -> DeadLetter | None:
        row = (
            await self._session.execute(
                select(DeadLetterModel).where(
                    DeadLetterModel.tenant_id == tenant_id.value,
                    DeadLetterModel.id == dead_letter_id.value,
                )
            )
        ).scalar_one_or_none()
        return _to_entity(row) if row else None

    async def list_for_tenant(
        self, tenant_id: TenantId, *, limit: int = 50, offset: int = 0
    ) -> tuple[list[DeadLetter], int]:
        total = (
            await self._session.execute(
                select(func.count())
                .select_from(DeadLetterModel)
                .where(DeadLetterModel.tenant_id == tenant_id.value)
            )
        ).scalar_one()
        rows = (
            await self._session.execute(
                select(DeadLetterModel)
                .where(DeadLetterModel.tenant_id == tenant_id.value)
                .order_by(DeadLetterModel.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
        ).scalars().all()
        return [_to_entity(r) for r in rows], total
