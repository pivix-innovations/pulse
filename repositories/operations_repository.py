from __future__ import annotations

from datetime import datetime

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import DeadLetterModel, DeliveryAttemptModel, OutboxEntryModel
from domain.entities.operations import OperationsSnapshot
from domain.enums import DeliveryStatus, OutboxStatus

_OPEN = (DeliveryStatus.PENDING, DeliveryStatus.RETRYING, DeliveryStatus.SENT)


class PostgresOperationsRepository:
    """Implements services.ports.repositories.OperationsRepository."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def snapshot(
        self, now: datetime, lost_before: datetime, in_doubt_before: datetime
    ) -> OperationsSnapshot:
        pending, oldest = (
            await self._session.execute(
                select(func.count(), func.min(OutboxEntryModel.created_at)).where(
                    OutboxEntryModel.status == OutboxStatus.PENDING
                )
            )
        ).one()

        rows = (
            await self._session.execute(
                select(
                    DeliveryAttemptModel.channel, DeliveryAttemptModel.status, func.count()
                )
                .where(DeliveryAttemptModel.status.in_(_OPEN))
                .group_by(DeliveryAttemptModel.channel, DeliveryAttemptModel.status)
            )
        ).all()

        due_at = func.coalesce(DeliveryAttemptModel.next_retry_at, DeliveryAttemptModel.updated_at)
        stuck = (
            await self._session.execute(
                select(func.count()).where(
                    DeliveryAttemptModel.status.in_([DeliveryStatus.PENDING, DeliveryStatus.RETRYING]),
                    or_(
                        and_(
                            DeliveryAttemptModel.dispatch_started_at.is_(None),
                            due_at < lost_before,
                        ),
                        DeliveryAttemptModel.dispatch_started_at < in_doubt_before,
                    ),
                )
            )
        ).scalar_one()

        dead = (await self._session.execute(select(func.count()).select_from(DeadLetterModel))).scalar_one()

        return OperationsSnapshot(
            outbox_pending=pending,
            outbox_oldest_age_seconds=max((now - oldest).total_seconds(), 0.0) if oldest else 0.0,
            open_attempts={(c, s): n for c, s, n in rows},
            stuck_attempts=stuck,
            dead_letters=dead,
        )
