from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.dependencies import get_uow, require_tenant
from api.v1.schemas import DeliveryAttemptOut, NotificationOut
from domain.enums import DeliveryStatus
from domain.value_objects.ids import DeliveryAttemptId, NotificationId, TenantId
from repositories.uow import SqlAlchemyUnitOfWork

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
    tenant_id: TenantId = Depends(require_tenant),
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
        await uow.commit()
        return AckOut(attempt_id=body.attempt_id, status=attempt.status)


@router.get("/{notification_id}", response_model=NotificationOut)
async def get_notification(
    notification_id: uuid.UUID,
    tenant_id: TenantId = Depends(require_tenant),
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
            )
            for a in attempts
        ],
    )
