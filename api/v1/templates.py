from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.dependencies import get_uow, require_tenant
from domain.entities.template import Template
from domain.enums import ChannelType, TemplateFormat
from domain.value_objects.ids import TemplateId, TenantId
from repositories.uow import SqlAlchemyUnitOfWork

router = APIRouter(prefix="/templates", tags=["templates"])


class TemplateIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    channel: ChannelType
    body: str = Field(min_length=1)
    subject: str | None = Field(default=None, max_length=500)
    format: TemplateFormat = TemplateFormat.MARKDOWN
    locale: str = Field(default="en", max_length=16)
    is_active: bool = True


class TemplateOut(TemplateIn):
    id: uuid.UUID
    created_at: datetime | None
    updated_at: datetime | None


def _out(t: Template) -> TemplateOut:
    return TemplateOut(
        id=t.id.value,
        name=t.name,
        channel=t.channel,
        body=t.body,
        subject=t.subject,
        format=t.format,
        locale=t.locale,
        is_active=t.is_active,
        created_at=t.created_at,
        updated_at=t.updated_at,
    )


@router.post("", response_model=TemplateOut, status_code=201)
async def create_template(
    body: TemplateIn,
    tenant_id: TenantId = Depends(require_tenant),
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
) -> TemplateOut:
    template = Template(id=TemplateId.new(), tenant_id=tenant_id, **body.model_dump())
    async with uow:
        await uow.templates.add(template)
        await uow.commit()
        return _out(await uow.templates.get(tenant_id, template.id))


@router.get("", response_model=list[TemplateOut])
async def list_templates(
    tenant_id: TenantId = Depends(require_tenant),
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
) -> list[TemplateOut]:
    async with uow:
        return [_out(t) for t in await uow.templates.list(tenant_id)]


@router.get("/{template_id}", response_model=TemplateOut)
async def get_template(
    template_id: uuid.UUID,
    tenant_id: TenantId = Depends(require_tenant),
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
) -> TemplateOut:
    async with uow:
        template = await uow.templates.get(tenant_id, TemplateId(template_id))
    if template is None:
        raise HTTPException(status_code=404, detail="Template not found")
    return _out(template)


@router.put("/{template_id}", response_model=TemplateOut)
async def update_template(
    template_id: uuid.UUID,
    body: TemplateIn,
    tenant_id: TenantId = Depends(require_tenant),
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
) -> TemplateOut:
    async with uow:
        existing = await uow.templates.get(tenant_id, TemplateId(template_id))
        if existing is None:
            raise HTTPException(status_code=404, detail="Template not found")
        updated = Template(id=existing.id, tenant_id=tenant_id, **body.model_dump())
        await uow.templates.update(updated)
        await uow.commit()
        return _out(await uow.templates.get(tenant_id, existing.id))


@router.delete("/{template_id}", status_code=204)
async def delete_template(
    template_id: uuid.UUID,
    tenant_id: TenantId = Depends(require_tenant),
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
) -> None:
    async with uow:
        deleted = await uow.templates.delete(tenant_id, TemplateId(template_id))
        await uow.commit()
    if not deleted:
        raise HTTPException(status_code=404, detail="Template not found")
