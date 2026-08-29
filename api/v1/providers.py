from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.dependencies import get_uow, require_tenant
from domain.entities.provider import Provider
from domain.enums import ChannelType
from domain.value_objects.ids import ProviderId, TenantId
from repositories.uow import SqlAlchemyUnitOfWork

router = APIRouter(prefix="/providers", tags=["providers"])


class ProviderIn(BaseModel):
    channel: ChannelType
    name: str = Field(min_length=1, max_length=100)
    # Non-secret configuration only; credentials stay in the environment.
    config: dict[str, Any] = Field(default_factory=dict)
    priority: int = 0
    is_active: bool = True


class ProviderOut(ProviderIn):
    id: uuid.UUID
    created_at: datetime | None
    updated_at: datetime | None


def _out(p: Provider) -> ProviderOut:
    return ProviderOut(
        id=p.id.value,
        channel=p.channel,
        name=p.name,
        config=p.config,
        priority=p.priority,
        is_active=p.is_active,
        created_at=p.created_at,
        updated_at=p.updated_at,
    )


@router.post("", response_model=ProviderOut, status_code=201)
async def create_provider(
    body: ProviderIn,
    tenant_id: TenantId = Depends(require_tenant),
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
) -> ProviderOut:
    provider = Provider(id=ProviderId.new(), tenant_id=tenant_id, **body.model_dump())
    async with uow:
        await uow.providers.add(provider)
        await uow.commit()
        return _out(await uow.providers.get(tenant_id, provider.id))


@router.get("", response_model=list[ProviderOut])
async def list_providers(
    tenant_id: TenantId = Depends(require_tenant),
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
) -> list[ProviderOut]:
    async with uow:
        return [_out(p) for p in await uow.providers.list(tenant_id)]


@router.get("/{provider_id}", response_model=ProviderOut)
async def get_provider(
    provider_id: uuid.UUID,
    tenant_id: TenantId = Depends(require_tenant),
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
) -> ProviderOut:
    async with uow:
        provider = await uow.providers.get(tenant_id, ProviderId(provider_id))
    if provider is None:
        raise HTTPException(status_code=404, detail="Provider not found")
    return _out(provider)


@router.put("/{provider_id}", response_model=ProviderOut)
async def update_provider(
    provider_id: uuid.UUID,
    body: ProviderIn,
    tenant_id: TenantId = Depends(require_tenant),
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
) -> ProviderOut:
    async with uow:
        existing = await uow.providers.get(tenant_id, ProviderId(provider_id))
        if existing is None:
            raise HTTPException(status_code=404, detail="Provider not found")
        updated = Provider(id=existing.id, tenant_id=tenant_id, **body.model_dump())
        await uow.providers.update(updated)
        await uow.commit()
        return _out(await uow.providers.get(tenant_id, existing.id))


@router.delete("/{provider_id}", status_code=204)
async def delete_provider(
    provider_id: uuid.UUID,
    tenant_id: TenantId = Depends(require_tenant),
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
) -> None:
    async with uow:
        deleted = await uow.providers.delete(tenant_id, ProviderId(provider_id))
        await uow.commit()
    if not deleted:
        raise HTTPException(status_code=404, detail="Provider not found")
