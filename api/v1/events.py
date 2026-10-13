from __future__ import annotations

import math

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from api.dependencies import get_notification_service, get_uow, require_scope
from api.v1.schemas import EventAccepted, EventIn, EventOut
from domain.value_objects.ids import TenantId
from telemetry.metrics import get_metrics
from services.ports.clients import SentinelUnavailable
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
    request: Request,
    response: Response,
    tenant_id: TenantId = Depends(require_scope("ingest")),
    service: NotificationService = Depends(get_notification_service),
) -> EventAccepted:
    limiter = getattr(request.app.state, "rate_limit_service", None)
    if limiter is not None:
        decision, limits = await limiter.check(tenant_id)
        if not decision.allowed:
            retry = max(1, math.ceil(decision.retry_after_seconds))
            get_metrics().ratelimit_rejected.add(1, {"reason": decision.reason or "rate"})
            what = "daily event quota" if decision.reason == "quota" else "request rate limit"
            raise HTTPException(
                status_code=429,
                detail=f"Your {what} has been reached; retry in {retry}s",
                headers={"Retry-After": str(retry), "X-RateLimit-Reason": decision.reason or "rate"},
            )
        if limits is not None:
            response.headers["X-RateLimit-Limit"] = f"{limits.rate_per_second:g}/s burst {limits.burst}"
            response.headers["X-RateLimit-Remaining"] = str(int(decision.tokens_remaining))
            if decision.quota_remaining is not None:
                response.headers["X-Quota-Remaining"] = str(decision.quota_remaining)
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
    except SentinelUnavailable as exc:
        # We could not find out who to notify. Not "nobody": ask the producer to retry.
        if limiter is not None:
            await limiter.refund(tenant_id)
        raise HTTPException(
            status_code=503,
            detail="The recipient directory is temporarily unavailable; retry shortly",
            headers={"Retry-After": "5"},
        ) from exc
    except (UnknownEventType, NoRecipientsResolved) as exc:
        if limiter is not None:  # a rejected event shouldn't eat the tenant's quota
            await limiter.refund(tenant_id)
        raise HTTPException(status_code=422, detail=str(exc))
    get_metrics().events_ingested.add(1, {"tenant": str(tenant_id)})
    return EventAccepted(notification_id=notification_id.value)


@router.get("", response_model=list[EventOut])
async def list_events(
    response: Response,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    tenant_id: TenantId = Depends(require_scope("read")),
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
