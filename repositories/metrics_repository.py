from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, literal_column, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import DeadLetterModel, DeliveryAttemptModel, NotificationModel
from domain.enums import ChannelType, DeliveryStatus
from domain.value_objects.ids import TenantId


class PostgresMetricsRepository:
    """Implements services.ports.repositories.MetricsRepository."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def attempts_by_channel_status(
        self, tenant_id: TenantId, since: datetime
    ) -> list[tuple[ChannelType, DeliveryStatus, int]]:
        stmt = (
            select(
                DeliveryAttemptModel.channel,
                DeliveryAttemptModel.status,
                func.count(),
            )
            .where(
                DeliveryAttemptModel.tenant_id == tenant_id.value,
                DeliveryAttemptModel.created_at >= since,
            )
            .group_by(DeliveryAttemptModel.channel, DeliveryAttemptModel.status)
        )
        return [(row[0], row[1], row[2]) for row in (await self._session.execute(stmt)).all()]

    async def attempts_timeline(
        self, tenant_id: TenantId, since: datetime, bucket_seconds: int
    ) -> list[tuple[datetime, DeliveryStatus, int]]:
        # bucket_seconds is an int chosen by the service, never user text, so inlining it is safe.
        bucket = func.date_bin(
            literal_column(f"INTERVAL '{int(bucket_seconds)} seconds'"),
            DeliveryAttemptModel.created_at,
            literal_column("TIMESTAMPTZ '2000-01-01 00:00:00+00'"),
        ).label("bucket")
        stmt = (
            select(bucket, DeliveryAttemptModel.status, func.count())
            .where(
                DeliveryAttemptModel.tenant_id == tenant_id.value,
                DeliveryAttemptModel.created_at >= since,
            )
            .group_by(bucket, DeliveryAttemptModel.status)
            .order_by(bucket)
        )
        return [(row[0], row[1], row[2]) for row in (await self._session.execute(stmt)).all()]

    async def notifications_by_status(
        self, tenant_id: TenantId, since: datetime
    ) -> list[tuple[str, int]]:
        stmt = (
            select(NotificationModel.status, func.count())
            .where(
                NotificationModel.tenant_id == tenant_id.value,
                NotificationModel.created_at >= since,
            )
            .group_by(NotificationModel.status)
        )
        return [(row[0].value, row[1]) for row in (await self._session.execute(stmt)).all()]

    async def dead_letter_count(self, tenant_id: TenantId, since: datetime) -> int:
        stmt = select(func.count()).where(
            DeadLetterModel.tenant_id == tenant_id.value,
            DeadLetterModel.created_at >= since,
        )
        return (await self._session.execute(stmt)).scalar_one()

    async def avg_confirmation_seconds(
        self, tenant_id: TenantId, since: datetime
    ) -> float | None:
        elapsed = func.extract(
            "epoch", DeliveryAttemptModel.delivered_at - DeliveryAttemptModel.sent_at
        )
        stmt = select(func.avg(elapsed)).where(
            DeliveryAttemptModel.tenant_id == tenant_id.value,
            DeliveryAttemptModel.created_at >= since,
            DeliveryAttemptModel.delivered_at.is_not(None),
            DeliveryAttemptModel.sent_at.is_not(None),
        )
        result = (await self._session.execute(stmt)).scalar_one()
        return float(result) if result is not None else None
