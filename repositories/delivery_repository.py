from __future__ import annotations

from datetime import datetime
from typing import Sequence

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import DeliveryAttemptModel
from domain.entities.delivery import DeliveryAttempt
from domain.enums import ChannelType, DeliveryStatus
from domain.value_objects.ids import (
    DeliveryAttemptId,
    NotificationId,
    ProviderId,
    RecipientId,
    TenantId,
)


def _to_entity(row: DeliveryAttemptModel) -> DeliveryAttempt:
    return DeliveryAttempt(
        id=DeliveryAttemptId(row.id),
        tenant_id=TenantId(row.tenant_id),
        notification_id=NotificationId(row.notification_id),
        recipient_id=RecipientId(row.recipient_id),
        channel=row.channel,
        status=row.status,
        provider_id=ProviderId(row.provider_id) if row.provider_id else None,
        attempt_count=row.attempt_count,
        subject=row.subject,
        body=row.body,
        body_html=row.body_html,
        last_error=row.last_error,
        sent_at=row.sent_at,
        delivered_at=row.delivered_at,
        next_retry_at=row.next_retry_at,
        dispatch_started_at=row.dispatch_started_at,
        expects_confirmation=row.expects_confirmation,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class PostgresDeliveryAttemptRepository:
    """Implements services.ports.repositories.DeliveryAttemptRepository."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_many(self, attempts: Sequence[DeliveryAttempt]) -> None:
        self._session.add_all(
            DeliveryAttemptModel(
                id=a.id.value,
                tenant_id=a.tenant_id.value,
                notification_id=a.notification_id.value,
                recipient_id=a.recipient_id.value,
                channel=a.channel,
                status=a.status,
                provider_id=a.provider_id.value if a.provider_id else None,
                attempt_count=a.attempt_count,
                subject=a.subject,
                body=a.body,
                body_html=a.body_html,
                next_retry_at=a.next_retry_at,
                expects_confirmation=a.expects_confirmation,
            )
            for a in attempts
        )
        await self._session.flush()

    async def get(
        self, tenant_id: TenantId, attempt_id: DeliveryAttemptId, *, for_update: bool = False
    ) -> DeliveryAttempt | None:
        stmt = select(DeliveryAttemptModel).where(
            DeliveryAttemptModel.tenant_id == tenant_id.value,
            DeliveryAttemptModel.id == attempt_id.value,
        )
        if for_update:
            stmt = stmt.with_for_update()
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return _to_entity(row) if row else None

    async def get_by_dispatch(
        self,
        notification_id: NotificationId,
        recipient_id: RecipientId,
        channel: ChannelType,
        *,
        for_update: bool = False,
    ) -> DeliveryAttempt | None:
        stmt = select(DeliveryAttemptModel).where(
            DeliveryAttemptModel.notification_id == notification_id.value,
            DeliveryAttemptModel.recipient_id == recipient_id.value,
            DeliveryAttemptModel.channel == channel,
        )
        if for_update:
            stmt = stmt.with_for_update()
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return _to_entity(row) if row else None

    async def list_stale(
        self,
        status: DeliveryStatus,
        channel: ChannelType,
        older_than: datetime,
        limit: int,
    ) -> list[DeliveryAttempt]:
        # `sent` staleness is measured from dispatch; other states from last update.
        timestamp = (
            DeliveryAttemptModel.sent_at
            if status is DeliveryStatus.SENT
            else DeliveryAttemptModel.updated_at
        )
        conditions = [
            DeliveryAttemptModel.status == status,
            DeliveryAttemptModel.channel == channel,
            timestamp < older_than,
        ]
        if status is DeliveryStatus.SENT:
            # `sent` is terminal for providers without delivery callbacks.
            conditions.append(DeliveryAttemptModel.expects_confirmation.is_(True))
        stmt = (
            select(DeliveryAttemptModel)
            .where(*conditions)
            .order_by(timestamp)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_to_entity(row) for row in rows]

    async def list_stuck(
        self, lost_before: datetime, in_doubt_before: datetime, limit: int
    ) -> list[DeliveryAttempt]:
        # Pending/retrying attempts that should have been worked by now:
        # either never claimed (queue job lost) or claimed with no outcome
        # recorded (worker died mid-send).
        due_at = func.coalesce(
            DeliveryAttemptModel.next_retry_at, DeliveryAttemptModel.updated_at
        )
        stmt = (
            select(DeliveryAttemptModel)
            .where(
                DeliveryAttemptModel.status.in_(
                    [DeliveryStatus.PENDING, DeliveryStatus.RETRYING]
                ),
                or_(
                    and_(
                        DeliveryAttemptModel.dispatch_started_at.is_(None),
                        due_at < lost_before,
                    ),
                    DeliveryAttemptModel.dispatch_started_at < in_doubt_before,
                ),
            )
            .order_by(due_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_to_entity(row) for row in rows]

    async def list_unconfirmed_for_recipient(
        self, tenant_id: TenantId, recipient_id: RecipientId, channel: ChannelType
    ) -> list[DeliveryAttempt]:
        stmt = (
            select(DeliveryAttemptModel)
            .where(
                DeliveryAttemptModel.tenant_id == tenant_id.value,
                DeliveryAttemptModel.recipient_id == recipient_id.value,
                DeliveryAttemptModel.channel == channel,
                DeliveryAttemptModel.status == DeliveryStatus.SENT,
            )
            .order_by(DeliveryAttemptModel.sent_at)
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_to_entity(row) for row in rows]

    async def list_for_notification(
        self, tenant_id: TenantId, notification_id: NotificationId
    ) -> list[DeliveryAttempt]:
        stmt = (
            select(DeliveryAttemptModel)
            .where(
                DeliveryAttemptModel.tenant_id == tenant_id.value,
                DeliveryAttemptModel.notification_id == notification_id.value,
            )
            .order_by(DeliveryAttemptModel.created_at)
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_to_entity(row) for row in rows]

    async def update(self, attempt: DeliveryAttempt) -> None:
        row = await self._session.get(DeliveryAttemptModel, attempt.id.value)
        if row is None or row.tenant_id != attempt.tenant_id.value:
            raise LookupError(f"DeliveryAttempt {attempt.id} not found for tenant")
        row.status = attempt.status
        row.provider_id = attempt.provider_id.value if attempt.provider_id else None
        row.attempt_count = attempt.attempt_count
        row.subject = attempt.subject
        row.body = attempt.body
        row.body_html = attempt.body_html
        row.last_error = attempt.last_error
        row.sent_at = attempt.sent_at
        row.delivered_at = attempt.delivered_at
        row.next_retry_at = attempt.next_retry_at
        row.dispatch_started_at = attempt.dispatch_started_at
        row.expects_confirmation = attempt.expects_confirmation
        await self._session.flush()
