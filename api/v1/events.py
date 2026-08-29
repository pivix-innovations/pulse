from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from api.dependencies import get_notification_service, require_tenant
from api.v1.schemas import EventAccepted, EventIn
from domain.value_objects.ids import TenantId
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
