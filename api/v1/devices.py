from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field

from api.dependencies import get_uow, require_scope
from domain.entities.device import DeviceToken
from domain.entities.recipient import Recipient
from domain.enums import DevicePlatform
from domain.value_objects.ids import DeviceTokenId, RecipientId, TenantId
from repositories.uow import SqlAlchemyUnitOfWork

router = APIRouter(prefix="/devices", tags=["devices"])


class DeviceRegistrationIn(BaseModel):
    external_ref: str = Field(min_length=1, max_length=255)
    platform: DevicePlatform
    token: str = Field(min_length=1, max_length=4096)


class DeviceRegistrationOut(BaseModel):
    id: uuid.UUID
    recipient_id: uuid.UUID


@router.post("", response_model=DeviceRegistrationOut, status_code=201)
async def register_device(
    body: DeviceRegistrationIn,
    tenant_id: TenantId = Depends(require_scope("client")),
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
) -> DeviceRegistrationOut:
    async with uow:
        # Get-or-create (not upsert): registration must not blank out the
        # contact details on an existing recipient snapshot.
        recipient = await uow.recipients.get_by_external_ref(tenant_id, body.external_ref)
        if recipient is None:
            recipient = await uow.recipients.upsert_by_external_ref(
                Recipient(id=RecipientId.new(), tenant_id=tenant_id, external_ref=body.external_ref)
            )
        token = await uow.device_tokens.upsert(
            DeviceToken(
                id=DeviceTokenId.new(),
                tenant_id=tenant_id,
                recipient_id=recipient.id,
                platform=body.platform,
                token=body.token,
            )
        )
        await uow.commit()
    return DeviceRegistrationOut(id=token.id.value, recipient_id=recipient.id.value)


class DeviceOut(BaseModel):
    id: uuid.UUID
    platform: DevicePlatform
    token_hint: str   # the last characters only: enough to tell devices apart, never the credential itself
    last_seen_at: datetime | None
    created_at: datetime | None


class DeviceUnregisterIn(BaseModel):
    token: str = Field(min_length=1, max_length=4096)


@router.get("", response_model=list[DeviceOut])
async def list_devices(
    recipient: str = Query(min_length=1, max_length=255, description="The recipient's external reference"),
    tenant_id: TenantId = Depends(require_scope("read", "client")),
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
) -> list[DeviceOut]:
    async with uow:
        row = await uow.recipients.get_by_external_ref(tenant_id, recipient)
        tokens = await uow.device_tokens.list_for_recipient(tenant_id, row.id) if row else []
    return [
        DeviceOut(
            id=t.id.value, platform=t.platform, token_hint="…" + t.token[-6:],
            last_seen_at=t.last_seen_at, created_at=t.created_at,
        )
        for t in tokens
    ]


@router.delete("", status_code=204)
async def unregister_device(
    body: DeviceUnregisterIn,
    tenant_id: TenantId = Depends(require_scope("client")),
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
) -> Response:
    """For a client app on logout: stop pushing to this device. Idempotent; the token travels in the body."""
    async with uow:
        await uow.device_tokens.delete_tokens(tenant_id, [body.token])
        await uow.commit()
    return Response(status_code=204)


@router.delete("/{device_id}", status_code=204)
async def delete_device(
    device_id: uuid.UUID,
    tenant_id: TenantId = Depends(require_scope("client")),
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
) -> Response:
    async with uow:
        if not await uow.device_tokens.delete(tenant_id, DeviceTokenId(device_id)):
            raise HTTPException(status_code=404, detail="Device not found")
        await uow.commit()
    return Response(status_code=204)
