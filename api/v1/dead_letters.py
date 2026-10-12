from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from api.dependencies import get_uow, require_tenant
from api.v1.schemas import DeadLetterOut
from domain.entities.outbox import OutboxEntry
from domain.enums import DeliveryStatus, NotificationStatus
from domain.value_objects.ids import DeadLetterId, OutboxEntryId, TenantId
from jobqueue.dispatch_guard import RedisDispatchGuard
from repositories.uow import SqlAlchemyUnitOfWork

router = APIRouter(prefix="/dead-letters", tags=["dead-letters"])


@router.get("", response_model=list[DeadLetterOut])
async def list_dead_letters(
    response: Response,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    tenant_id: TenantId = Depends(require_tenant),
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
) -> list[DeadLetterOut]:
    async with uow:
        items, total = await uow.dead_letters.list_for_tenant(tenant_id, limit=limit, offset=offset)
    response.headers["X-Total-Count"] = str(total)
    return [
        DeadLetterOut(
            id=d.id.value,
            attempt_id=d.attempt_id.value,
            notification_id=d.notification_id.value,
            channel=d.channel,
            reason=d.reason,
            created_at=d.created_at,
        )
        for d in items
    ]


@router.post("/{dead_letter_id}/retry", status_code=202)
async def retry_dead_letter(
    dead_letter_id: uuid.UUID,
    request: Request,
    tenant_id: TenantId = Depends(require_tenant),
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
) -> dict[str, str]:
    """Redrive: reset the dead attempt to pending and re-publish it via the outbox."""
    async with uow:
        dead_letter = await uow.dead_letters.get(tenant_id, DeadLetterId(dead_letter_id))
        if dead_letter is None:
            raise HTTPException(status_code=404, detail="Dead letter not found")
        attempt = await uow.delivery_attempts.get(tenant_id, dead_letter.attempt_id, for_update=True)
        if attempt is None:
            raise HTTPException(status_code=404, detail="Delivery attempt not found")
        if attempt.status is not DeliveryStatus.DEAD:
            raise HTTPException(
                status_code=409,
                detail=f"Attempt is '{attempt.status.value}', only dead attempts can be retried",
            )

        attempt.mark_replayed()
        await uow.delivery_attempts.update(attempt)
        notification = await uow.notifications.get(tenant_id, attempt.notification_id)
        if notification is not None and notification.status is not NotificationStatus.PROCESSING:
            notification.mark_processing()
            await uow.notifications.update(notification)
        await uow.outbox.add_many(
            [
                OutboxEntry(
                    id=OutboxEntryId.new(),
                    tenant_id=tenant_id,
                    aggregate_type="delivery_attempt",
                    aggregate_id=str(attempt.id),
                    topic=f"deliver.{attempt.channel.value}",
                    payload={
                        "attempt_id": str(attempt.id),
                        "tenant_id": str(tenant_id),
                        "channel": attempt.channel.value,
                    },
                )
            ]
        )
        await uow.commit()

    # A completed-send marker would make the redrive look like a duplicate.
    await RedisDispatchGuard(request.app.state.redis).release(attempt.idempotency_key)
    return {"attempt_id": str(attempt.id), "status": attempt.status.value}
