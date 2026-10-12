from __future__ import annotations

import uuid
from datetime import time

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from api.dependencies import get_uow, require_tenant
from domain.entities.preference import Preference
from domain.entities.recipient import Recipient
from domain.enums import ChannelType
from domain.value_objects.ids import PreferenceId, RecipientId, TenantId
from domain.value_objects.quiet_hours import QuietHours
from repositories.uow import SqlAlchemyUnitOfWork

router = APIRouter(prefix="/preferences", tags=["preferences"])


class QuietHoursIn(BaseModel):
    start: time
    end: time
    timezone: str = "UTC"


class PreferenceIn(BaseModel):
    external_ref: str = Field(min_length=1, max_length=255)
    channel: ChannelType
    opted_in: bool = True
    quiet_hours: QuietHoursIn | None = None


class PreferenceOut(BaseModel):
    id: uuid.UUID
    recipient_id: uuid.UUID
    channel: ChannelType
    opted_in: bool
    quiet_hours: QuietHoursIn | None


def _out(p: Preference) -> PreferenceOut:
    return PreferenceOut(
        id=p.id.value,
        recipient_id=p.recipient_id.value,
        channel=p.channel,
        opted_in=p.opted_in,
        quiet_hours=(
            QuietHoursIn(
                start=p.quiet_hours.start, end=p.quiet_hours.end, timezone=p.quiet_hours.timezone
            )
            if p.quiet_hours
            else None
        ),
    )


@router.put("", response_model=PreferenceOut)
async def upsert_preference(
    body: PreferenceIn,
    tenant_id: TenantId = Depends(require_tenant),
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
) -> PreferenceOut:
    async with uow:
        recipient = await uow.recipients.get_by_external_ref(tenant_id, body.external_ref)
        if recipient is None:
            recipient = await uow.recipients.upsert_by_external_ref(
                Recipient(id=RecipientId.new(), tenant_id=tenant_id, external_ref=body.external_ref)
            )
        preference = Preference(
            id=PreferenceId.new(),
            tenant_id=tenant_id,
            recipient_id=recipient.id,
            channel=body.channel,
            opted_in=body.opted_in,
            quiet_hours=(
                QuietHours(
                    start=body.quiet_hours.start,
                    end=body.quiet_hours.end,
                    timezone=body.quiet_hours.timezone,
                )
                if body.quiet_hours
                else None
            ),
        )
        await uow.preferences.upsert(preference)
        await uow.commit()
        stored = next(
            p
            for p in await uow.preferences.list_for_recipient(tenant_id, recipient.id)
            if p.channel is body.channel
        )
        return _out(stored)


@router.get("", response_model=list[PreferenceOut])
async def list_preferences(
    recipient: str = Query(min_length=1, max_length=255),
    tenant_id: TenantId = Depends(require_tenant),
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
) -> list[PreferenceOut]:
    async with uow:
        row = await uow.recipients.get_by_external_ref(tenant_id, recipient)
        if row is None:
            raise HTTPException(status_code=404, detail="Unknown recipient")
        return [_out(p) for p in await uow.preferences.list_for_recipient(tenant_id, row.id)]


@router.delete("/{preference_id}", status_code=204)
async def delete_preference(
    preference_id: uuid.UUID,
    tenant_id: TenantId = Depends(require_tenant),
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
) -> None:
    async with uow:
        deleted = await uow.preferences.delete(tenant_id, PreferenceId(preference_id))
        await uow.commit()
    if not deleted:
        raise HTTPException(status_code=404, detail="Preference not found")
