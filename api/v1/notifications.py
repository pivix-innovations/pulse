from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel

from api.dependencies import get_uow, require_scope
from api.v1.schemas import DeliveryAttemptOut, NotificationOut, NotificationSummaryOut
from domain.enums import DeliveryStatus, NotificationStatus
from domain.value_objects.ids import DeliveryAttemptId, NotificationId, TenantId
from repositories.uow import SqlAlchemyUnitOfWork
from services.settlement import settle_notification

router = APIRouter(prefix="/notifications", tags=["notifications"])


class AckIn(BaseModel):
    attempt_id: uuid.UUID


class AckOut(BaseModel):
    attempt_id: uuid.UUID
    status: DeliveryStatus


@router.post("/{notification_id}/ack", response_model=AckOut)
async def ack_notification(
    notification_id: uuid.UUID,
    body: AckIn,
    tenant_id: TenantId = Depends(require_scope("client")),
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
) -> AckOut:
    """
    First-party client confirmation (SSE onmessage / push app code): the
    receiving end really got it -- moves the attempt from sent to delivered.
    """
    async with uow:
        attempt = await uow.delivery_attempts.get(tenant_id, DeliveryAttemptId(body.attempt_id))
        if attempt is None or attempt.notification_id.value != notification_id:
            raise HTTPException(status_code=404, detail="Delivery attempt not found")
        if attempt.status is DeliveryStatus.DELIVERED:
            return AckOut(attempt_id=body.attempt_id, status=attempt.status)  # idempotent
        if attempt.status is not DeliveryStatus.SENT:
            raise HTTPException(
                status_code=409,
                detail=f"Cannot ack an attempt in state '{attempt.status.value}'",
            )
        attempt.mark_delivered(datetime.now(timezone.utc))
        await uow.delivery_attempts.update(attempt)
        await settle_notification(uow, tenant_id, attempt.notification_id)
        await uow.commit()
        return AckOut(attempt_id=body.attempt_id, status=attempt.status)


@router.get("", response_model=list[NotificationSummaryOut])
async def list_notifications(
    response: Response,
    status: NotificationStatus | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    tenant_id: TenantId = Depends(require_scope("read")),
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
) -> list[NotificationSummaryOut]:
    async with uow:
        items, total = await uow.notifications.list_for_tenant(
            tenant_id, status=status, limit=limit, offset=offset
        )
    response.headers["X-Total-Count"] = str(total)
    return [
        NotificationSummaryOut(
            id=n.id.value,
            status=n.status,
            event_id=n.event_id.value,
            workflow_id=n.workflow_id.value,
            scheduled_at=n.scheduled_at,
            created_at=n.created_at,
        )
        for n in items
    ]


@router.get("/{notification_id}", response_model=NotificationOut)
async def get_notification(
    notification_id: uuid.UUID,
    tenant_id: TenantId = Depends(require_scope("read")),
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
) -> NotificationOut:
    async with uow:
        notification = await uow.notifications.get(tenant_id, NotificationId(notification_id))
        if notification is None:
            raise HTTPException(status_code=404, detail="Notification not found")
        attempts = await uow.delivery_attempts.list_for_notification(tenant_id, notification.id)

    return NotificationOut(
        id=notification.id.value,
        status=notification.status,
        event_id=notification.event_id.value,
        workflow_id=notification.workflow_id.value,
        created_at=notification.created_at,
        attempts=[
            DeliveryAttemptOut(
                id=a.id.value,
                recipient_id=a.recipient_id.value,
                channel=a.channel,
                status=a.status,
                attempt_count=a.attempt_count,
                last_error=a.last_error,
                sent_at=a.sent_at,
                delivered_at=a.delivered_at,
                provider_id=a.provider_id.value if a.provider_id else None,
                provider_message_id=a.provider_message_id,
                awaiting_confirmation=a.status is DeliveryStatus.SENT and a.expects_confirmation,
            )
            for a in attempts
        ],
    )
