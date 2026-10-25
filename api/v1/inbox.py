from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from api.dependencies import require_scope
from domain.value_objects.ids import InboxMessageId, TenantId
from services.inbox_service import STATES, InboxNotFound, InboxService, InvalidCursor

router = APIRouter(prefix="/inbox", tags=["inbox"])

Recipient = Query(min_length=1, max_length=255, description="The recipient's external reference")


class InboxMessageOut(BaseModel):
    id: uuid.UUID
    notification_id: uuid.UUID
    title: str | None
    body: str
    is_read: bool
    read_at: datetime | None
    is_archived: bool
    created_at: datetime | None


class InboxPageOut(BaseModel):
    items: list[InboxMessageOut]
    next_cursor: str | None
    unread_count: int


class ChangedOut(BaseModel):
    changed: int
    unread_count: int


def get_inbox_service(request: Request) -> InboxService:
    return request.app.state.inbox_service


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="Message not found")


@router.get("", response_model=InboxPageOut)
async def list_inbox(
    recipient: str = Recipient,
    state: str = Query(default="active", description="active (default), unread, read, archived or all"),
    limit: int = Query(default=30, ge=1, le=100),
    cursor: str | None = None,
    tenant_id: TenantId = Depends(require_scope("client")),
    service: InboxService = Depends(get_inbox_service),
) -> InboxPageOut:
    """A recipient's messages, newest first, with the unread badge count."""
    if state not in STATES:
        raise HTTPException(status_code=422, detail=f"state must be one of {', '.join(STATES)}")
    try:
        page = await service.list(tenant_id, recipient, state=state, limit=limit, cursor=cursor)
    except InvalidCursor:
        raise HTTPException(status_code=422, detail="Malformed cursor")
    return InboxPageOut(
        items=[
            InboxMessageOut(
                id=m.id.value, notification_id=m.notification_id.value, title=m.title, body=m.body,
                is_read=m.is_read, read_at=m.read_at, is_archived=m.is_archived, created_at=m.created_at,
            )
            for m in page.items
        ],
        next_cursor=page.next_cursor,
        unread_count=page.unread_count,
    )


@router.get("/unread-count")
async def unread_count(
    recipient: str = Recipient,
    tenant_id: TenantId = Depends(require_scope("client")),
    service: InboxService = Depends(get_inbox_service),
) -> dict[str, int]:
    return {"unread_count": await service.unread_count(tenant_id, recipient)}


@router.post("/read-all", response_model=ChangedOut)
async def read_all(
    recipient: str = Recipient,
    tenant_id: TenantId = Depends(require_scope("client")),
    service: InboxService = Depends(get_inbox_service),
) -> ChangedOut:
    try:
        changed = await service.mark_read(tenant_id, recipient, None)
    except InboxNotFound:
        changed = 0
    return ChangedOut(changed=changed, unread_count=await service.unread_count(tenant_id, recipient))


async def _act(call, service: InboxService, tenant_id: TenantId, recipient: str) -> ChangedOut:
    try:
        changed = await call()
    except InboxNotFound:
        raise _not_found()
    return ChangedOut(changed=changed, unread_count=await service.unread_count(tenant_id, recipient))


@router.post("/{message_id}/read", response_model=ChangedOut)
async def mark_read(
    message_id: uuid.UUID,
    recipient: str = Recipient,
    tenant_id: TenantId = Depends(require_scope("client")),
    service: InboxService = Depends(get_inbox_service),
) -> ChangedOut:
    return await _act(
        lambda: service.mark_read(tenant_id, recipient, [InboxMessageId(message_id)]),
        service, tenant_id, recipient,
    )


async def _flag(fn, message_id, recipient, tenant_id, service) -> ChangedOut:
    async def call() -> int:
        await fn(tenant_id, recipient, InboxMessageId(message_id))
        return 1

    return await _act(call, service, tenant_id, recipient)


@router.post("/{message_id}/unread", response_model=ChangedOut)
async def mark_unread(
    message_id: uuid.UUID,
    recipient: str = Recipient,
    tenant_id: TenantId = Depends(require_scope("client")),
    service: InboxService = Depends(get_inbox_service),
) -> ChangedOut:
    return await _flag(service.mark_unread, message_id, recipient, tenant_id, service)


@router.post("/{message_id}/archive", response_model=ChangedOut)
async def archive(
    message_id: uuid.UUID,
    recipient: str = Recipient,
    tenant_id: TenantId = Depends(require_scope("client")),
    service: InboxService = Depends(get_inbox_service),
) -> ChangedOut:
    async def fn(t, r, m):
        await service.set_archived(t, r, m, True)

    return await _flag(fn, message_id, recipient, tenant_id, service)


@router.post("/{message_id}/unarchive", response_model=ChangedOut)
async def unarchive(
    message_id: uuid.UUID,
    recipient: str = Recipient,
    tenant_id: TenantId = Depends(require_scope("client")),
    service: InboxService = Depends(get_inbox_service),
) -> ChangedOut:
    async def fn(t, r, m):
        await service.set_archived(t, r, m, False)

    return await _flag(fn, message_id, recipient, tenant_id, service)
