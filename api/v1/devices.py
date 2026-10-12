from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from api.dependencies import get_uow, require_tenant
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
    tenant_id: TenantId = Depends(require_tenant),
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
