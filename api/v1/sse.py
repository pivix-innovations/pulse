from __future__ import annotations

import json
from typing import AsyncIterator

from fastapi import APIRouter, Cookie, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from api.dependencies import get_uow, resolve_env_or_db_api_key
from domain.enums import ChannelType
from domain.entities.recipient import Recipient
from domain.value_objects.ids import InvalidIdError, RecipientId, TenantId
from services.identity_service import AuthenticationError, can_access_tenant

router = APIRouter(prefix="/sse", tags=["sse"])

HEARTBEAT_SECONDS = 15.0


def _sse_event(event_id: str, data: dict) -> str:
    return f"id: {event_id}\nevent: notification\ndata: {json.dumps(data)}\n\n"


@router.get("/stream")
async def stream(
    request: Request,
    recipient: str = Query(min_length=1, max_length=255),
    api_key: str | None = Query(default=None),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    tenant: str | None = Query(default=None),
    pulse_session: str | None = Cookie(default=None),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    replay: bool = Query(default=False),
) -> StreamingResponse:
    """
    In-app delivery stream. On reconnect (standard Last-Event-ID header, or
    ?replay=1) it replays the recipient's sent-but-unconfirmed notifications
    before going live -- the client may have missed them.

    Auth: an API key (header, or query since EventSource cannot set
    headers), or a console session cookie plus ?tenant=<uuid>.
    """
    key = x_api_key or api_key
    tenant_id: TenantId | None = None
    if key:
        tenant_id = await resolve_env_or_db_api_key(request, key)
        if tenant_id is None:
            raise HTTPException(status_code=401, detail="Invalid API key")
    elif pulse_session and tenant:
        try:
            auth = await request.app.state.identity_service.resolve_session(pulse_session)
            tenant_id = TenantId.from_str(tenant)
        except (AuthenticationError, InvalidIdError):
            raise HTTPException(status_code=401, detail="Invalid session or tenant")
        if not can_access_tenant(auth, tenant_id):
            raise HTTPException(status_code=403, detail="Not a member of this tenant")
    else:
        raise HTTPException(status_code=401, detail="Provide an API key or sign in")

    uow = get_uow(request)
    async with uow:
        recipient_row = await uow.recipients.get_by_external_ref(tenant_id, recipient)
        if recipient_row is None:
            # Clients may connect before their first notification exists.
            recipient_row = await uow.recipients.upsert_by_external_ref(
                Recipient(id=RecipientId.new(), tenant_id=tenant_id, external_ref=recipient)
            )
            await uow.commit()
        replay_attempts = (
            await uow.delivery_attempts.list_unconfirmed_for_recipient(
                tenant_id, recipient_row.id, ChannelType.IN_APP
            )
            if (last_event_id is not None or replay)
            else []
        )

    registry = request.app.state.sse_registry

    async def event_stream() -> AsyncIterator[str]:
        for attempt in replay_attempts:
            yield _sse_event(
                str(attempt.id),
                {
                    "id": str(attempt.id),
                    "notification_id": str(attempt.notification_id),
                    "title": attempt.subject,
                    "body": attempt.body,
                    "replayed": True,
                },
            )
        async with registry.subscribe(tenant_id, recipient_row.id) as subscription:
            while True:
                if await request.is_disconnected():
                    return
                message = await subscription.get(timeout=HEARTBEAT_SECONDS)
                if message is None:
                    yield ": ping\n\n"  # heartbeat keeps proxies from closing us
                    continue
                data = json.loads(message)
                yield _sse_event(str(data.get("id", "")), data)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
