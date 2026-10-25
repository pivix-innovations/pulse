from __future__ import annotations

import re
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from api.dependencies import get_uow, require_scope
from domain.entities.category_preference import ALL_CHANNELS, CategoryPreference
from domain.entities.recipient import Recipient
from domain.enums import ChannelType
from domain.value_objects.ids import CategoryPreferenceId, RecipientId, TenantId
from repositories.uow import SqlAlchemyUnitOfWork

router = APIRouter(prefix="/category-preferences", tags=["preferences"])

_SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")


class CategoryPreferenceIn(BaseModel):
    external_ref: str = Field(min_length=1, max_length=255)
    category: str
    channel: ChannelType | None = Field(default=None, description="Omit for all channels")
    opted_in: bool

    @field_validator("category")
    @classmethod
    def _slug(cls, v: str) -> str:
        if not _SLUG.match(v):
            raise ValueError("lowercase letters, digits, '-' or '_', up to 40 characters")
        return v


class CategoryPreferenceOut(BaseModel):
    id: uuid.UUID
    recipient_id: uuid.UUID
    category: str
    channel: str
    opted_in: bool


def _out(p: CategoryPreference) -> CategoryPreferenceOut:
    return CategoryPreferenceOut(
        id=p.id.value, recipient_id=p.recipient_id.value, category=p.category, channel=p.channel, opted_in=p.opted_in
    )


@router.put("", response_model=CategoryPreferenceOut)
async def set_category_preference(
    body: CategoryPreferenceIn,
    tenant_id: TenantId = Depends(require_scope("client")),
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
) -> CategoryPreferenceOut:
    """Opt a recipient in or out of a category, on one channel or on all of them."""
    channel = body.channel.value if body.channel else ALL_CHANNELS
    async with uow:
        recipient = await uow.recipients.get_by_external_ref(tenant_id, body.external_ref)
        if recipient is None:
            recipient = await uow.recipients.upsert_by_external_ref(
                Recipient(id=RecipientId.new(), tenant_id=tenant_id, external_ref=body.external_ref)
            )
        await uow.category_preferences.upsert(CategoryPreference(
            id=CategoryPreferenceId.new(), tenant_id=tenant_id, recipient_id=recipient.id,
            category=body.category, channel=channel, opted_in=body.opted_in,
        ))
        await uow.commit()
        stored = next(
            p for p in await uow.category_preferences.list_for_recipient(tenant_id, recipient.id)
            if p.category == body.category and p.channel == channel
        )
        return _out(stored)


@router.get("", response_model=list[CategoryPreferenceOut])
async def list_category_preferences(
    recipient: str = Query(min_length=1, max_length=255),
    tenant_id: TenantId = Depends(require_scope("read", "client")),
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
) -> list[CategoryPreferenceOut]:
    async with uow:
        row = await uow.recipients.get_by_external_ref(tenant_id, recipient)
        if row is None:
            raise HTTPException(status_code=404, detail="Unknown recipient")
        return [_out(p) for p in await uow.category_preferences.list_for_recipient(tenant_id, row.id)]


@router.delete("/{preference_id}", status_code=204)
async def delete_category_preference(
    preference_id: uuid.UUID,
    tenant_id: TenantId = Depends(require_scope("client")),
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
) -> None:
    """Forget the choice: the recipient goes back to receiving the category."""
    async with uow:
        deleted = await uow.category_preferences.delete(tenant_id, CategoryPreferenceId(preference_id))
        await uow.commit()
    if not deleted:
        raise HTTPException(status_code=404, detail="Preference not found")
