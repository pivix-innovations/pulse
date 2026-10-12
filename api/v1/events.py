from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from api.dependencies import get_notification_service, get_uow, require_tenant
from api.v1.schemas import EventAccepted, EventIn, EventOut
from domain.value_objects.ids import TenantId
from repositories.uow import SqlAlchemyUnitOfWork
from services.notification_service import (
    IncomingEvent,
    NoRecipientsResolved,
    NotificationService,
    UnknownEventType,
)

router = APIRouter(prefix="/events", tags=["events"])


@router.post("", response_model=EventAccepted, status_code=202)
async def ingest_event(
    body: EventIn,
    tenant_id: TenantId = Depends(require_tenant),
    service: NotificationService = Depends(get_notification_service),
) -> EventAccepted:
    try:
        notification_id = await service.process_event(
            tenant_id,
            IncomingEvent(
                event_type=body.event_type,
                source=body.source,
                payload=body.payload,
                idempotency_key=body.idempotency_key,
                occurred_at=body.occurred_at,
                scheduled_at=body.scheduled_at,
            ),
        )
    except UnknownEventType as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except NoRecipientsResolved as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return EventAccepted(notification_id=notification_id.value)


@router.get("", response_model=list[EventOut])
async def list_events(
    response: Response,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    tenant_id: TenantId = Depends(require_tenant),
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
) -> list[EventOut]:
    async with uow:
        items, total = await uow.events.list_for_tenant(tenant_id, limit=limit, offset=offset)
    response.headers["X-Total-Count"] = str(total)
    return [
        EventOut(
            id=e.id.value,
            event_type=e.event_type,
            source=e.source,
            payload=e.payload,
            idempotency_key=e.idempotency_key,
            occurred_at=e.occurred_at,
            received_at=e.received_at,
        )
        for e in items
    ]
