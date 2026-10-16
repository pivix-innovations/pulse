from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel

from api.dependencies import get_uow, require_scope
from api.v1.schemas import DeliveryAttemptOut, NotificationOut, DigestOut, NotificationSummaryOut, StepRunOut
from domain.enums import ChannelType, DeliveryStatus, NotificationStatus
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


class CancelOut(BaseModel):
    cancelled: int
    status: NotificationStatus


@router.post("/{notification_id}/cancel-sequence", response_model=CancelOut)
async def cancel_sequence(
    notification_id: uuid.UUID,
    tenant_id: TenantId = Depends(require_scope("admin")),
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
) -> CancelOut:
    """
    Stop the steps of a sequence that have not run yet (every recipient). Steps that already ran
    and their deliveries are untouched; the waiting steps are recorded as skipped. Idempotent:
    with nothing waiting, `cancelled` is 0.
    """
    async with uow:
        notification = await uow.notifications.get(tenant_id, NotificationId(notification_id))
        if notification is None:
            raise HTTPException(status_code=404, detail="Notification not found")
        cancelled = await uow.step_runs.cancel_open(
            notification.id, "cancelled by an operator", datetime.now(timezone.utc)
        )
        if cancelled:
            await settle_notification(uow, tenant_id, notification.id)   # nothing left to wait for
            await uow.commit()
            notification = await uow.notifications.get(tenant_id, notification.id) or notification
    return CancelOut(cancelled=cancelled, status=notification.status)


@router.get("", response_model=list[NotificationSummaryOut])
async def list_notifications(
    response: Response,
    status: NotificationStatus | None = None,
    event_type: str | None = Query(default=None, max_length=255, description="Only notifications for this event type"),
    recipient: str | None = Query(default=None, max_length=255, description="Only notifications with an attempt for this recipient (external ref)"),
    channel: ChannelType | None = Query(default=None, description="Only notifications with an attempt on this channel"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    tenant_id: TenantId = Depends(require_scope("read")),
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
) -> list[NotificationSummaryOut]:
    async with uow:
        recipient_id = None
        if recipient is not None:
            row = await uow.recipients.get_by_external_ref(tenant_id, recipient)
            if row is None:   # nobody by that name: nothing can match
                response.headers["X-Total-Count"] = "0"
                return []
            recipient_id = row.id
        items, total = await uow.notifications.list_for_tenant(
            tenant_id, status=status, event_type=event_type, recipient_id=recipient_id,
            channel=channel, limit=limit, offset=offset,
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
        step_runs = await uow.step_runs.list_for_notification(tenant_id, notification.id)
        batch = await uow.digests.get_for_notification(tenant_id, notification.id)

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
                template_id=a.template_id.value if a.template_id else None,
                template_version=a.template_version,
            )
            for a in attempts
        ],
        digest=DigestOut(
            batch_id=batch.id.value, status=batch.status.value, flush_at=batch.flush_at,
            item_count=batch.item_count, is_anchor=batch.anchor_notification_id == notification.id,
            anchor_notification_id=batch.anchor_notification_id.value,
        ) if batch else None,
        steps=[
            StepRunOut(
                recipient_id=r.recipient_id.value, step_id=r.step_id, step_index=r.step_index,
                status=r.status.value, run_at=r.run_at, executed_at=r.executed_at,
                attempt_id=r.attempt_id.value if r.attempt_id else None, reason=r.reason,
            )
            for r in step_runs
        ],
    )
