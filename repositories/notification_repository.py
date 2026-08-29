from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import NotificationModel
from domain.entities.notification import Notification
from domain.enums import NotificationStatus
from domain.value_objects.ids import EventId, NotificationId, TenantId, WorkflowId


def _to_entity(row: NotificationModel) -> Notification:
    return Notification(
        id=NotificationId(row.id),
        tenant_id=TenantId(row.tenant_id),
        event_id=EventId(row.event_id),
        workflow_id=WorkflowId(row.workflow_id),
        status=row.status,
        payload=dict(row.payload),
        scheduled_at=row.scheduled_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class PostgresNotificationRepository:
    """Implements services.ports.repositories.NotificationRepository."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, notification: Notification) -> None:
        self._session.add(
            NotificationModel(
                id=notification.id.value,
                tenant_id=notification.tenant_id.value,
                event_id=notification.event_id.value,
                workflow_id=notification.workflow_id.value,
                status=notification.status,
                payload=notification.payload,
                scheduled_at=notification.scheduled_at,
            )
        )
        await self._session.flush()

    async def get(self, tenant_id: TenantId, notification_id: NotificationId) -> Notification | None:
        stmt = select(NotificationModel).where(
            NotificationModel.tenant_id == tenant_id.value,
            NotificationModel.id == notification_id.value,
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return _to_entity(row) if row else None

    async def get_by_event(self, tenant_id: TenantId, event_id: EventId) -> Notification | None:
        stmt = select(NotificationModel).where(
            NotificationModel.tenant_id == tenant_id.value,
            NotificationModel.event_id == event_id.value,
        )
        row = (await self._session.execute(stmt)).scalars().first()
        return _to_entity(row) if row else None

    async def list_due(self, now: datetime, limit: int) -> list[Notification]:
        stmt = (
            select(NotificationModel)
            .where(
                NotificationModel.status == NotificationStatus.CREATED,
                NotificationModel.scheduled_at.is_not(None),
                NotificationModel.scheduled_at <= now,
            )
            .order_by(NotificationModel.scheduled_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_to_entity(row) for row in rows]

    async def update(self, notification: Notification) -> None:
        row = await self._session.get(NotificationModel, notification.id.value)
        if row is None or row.tenant_id != notification.tenant_id.value:
            raise LookupError(f"Notification {notification.id} not found for tenant")
        row.status = notification.status
        row.payload = notification.payload
        row.scheduled_at = notification.scheduled_at
        await self._session.flush()
