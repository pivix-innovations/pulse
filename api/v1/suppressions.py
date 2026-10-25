from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from api.dependencies import get_uow, require_scope
from domain.entities.suppression import Suppression, SuppressionReason
from domain.enums import ChannelType
from domain.value_objects.ids import SuppressionId, TenantId
from repositories.uow import SqlAlchemyUnitOfWork

router = APIRouter(prefix="/suppressions", tags=["suppressions"])


class SuppressionIn(BaseModel):
    channel: ChannelType
    address: str = Field(min_length=3, max_length=320)
    reason: SuppressionReason = SuppressionReason.MANUAL
    detail: str | None = Field(default=None, max_length=500)


class SuppressionOut(BaseModel):
    id: uuid.UUID
    channel: ChannelType
    address: str
    reason: SuppressionReason
    detail: str | None
    created_at: datetime | None


def _out(s: Suppression) -> SuppressionOut:
    return SuppressionOut(
        id=s.id.value, channel=s.channel, address=s.address, reason=s.reason, detail=s.detail, created_at=s.created_at
    )


@router.get("", response_model=list[SuppressionOut])
async def list_suppressions(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    tenant_id: TenantId = Depends(require_scope("read")),
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
) -> list[SuppressionOut]:
    async with uow:
        return [_out(s) for s in await uow.suppressions.list(tenant_id, limit=limit, offset=offset)]


@router.post("", response_model=SuppressionOut, status_code=201)
async def add_suppression(
    body: SuppressionIn,
    tenant_id: TenantId = Depends(require_scope("admin")),
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
) -> SuppressionOut:
    """Block an address by hand (bounces, complaints and STOP replies are added automatically)."""
    if body.channel not in (ChannelType.EMAIL, ChannelType.SMS):
        raise HTTPException(status_code=422, detail="Only email and sms addresses can be suppressed")
    suppression = Suppression(
        id=SuppressionId.new(), tenant_id=tenant_id, channel=body.channel,
        address=body.address, reason=body.reason, detail=body.detail,
    )
    async with uow:
        if not await uow.suppressions.add(suppression):
            raise HTTPException(status_code=409, detail="Address is already suppressed")
        await uow.commit()
        stored = next(
            s for s in await uow.suppressions.list(tenant_id, limit=200) if s.id == suppression.id
        )
    return _out(stored)


@router.delete("/{suppression_id}", status_code=204)
async def remove_suppression(
    suppression_id: uuid.UUID,
    tenant_id: TenantId = Depends(require_scope("admin")),
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
) -> None:
    async with uow:
        deleted = await uow.suppressions.delete(tenant_id, SuppressionId(suppression_id))
        await uow.commit()
    if not deleted:
        raise HTTPException(status_code=404, detail="Suppression not found")
