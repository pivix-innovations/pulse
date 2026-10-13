from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.dependencies import get_uow, require_scope
from domain.entities.workflow import Workflow
from domain.enums import ChannelType
from domain.value_objects.ids import InvalidIdError, ProviderId, TenantId, WorkflowId
from repositories.uow import SqlAlchemyUnitOfWork
from workflows.registry import WorkflowDefinition

router = APIRouter(prefix="/workflows", tags=["workflows"])


class WorkflowIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    event_type: str = Field(min_length=1, max_length=200)
    definition: dict[str, Any] = Field(default_factory=dict)
    is_active: bool = True


class WorkflowOut(WorkflowIn):
    id: uuid.UUID
    created_at: datetime | None
    updated_at: datetime | None


def _out(w: Workflow) -> WorkflowOut:
    return WorkflowOut(
        id=w.id.value,
        name=w.name,
        event_type=w.event_type,
        definition=w.definition,
        is_active=w.is_active,
        created_at=w.created_at,
        updated_at=w.updated_at,
    )


def _validate_definition(body: WorkflowIn) -> None:
    """The definition must parse into channel templates + fallbacks."""
    try:
        parsed = WorkflowDefinition.from_dict(body.name, body.event_type, body.definition)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"Invalid workflow definition: {exc}")
    if not parsed.channels:
        raise HTTPException(status_code=422, detail="Workflow definition needs at least one channel")
    known = {ct.channel.value for ct in (*parsed.channels, *parsed.fallback_templates)}
    for source, target in parsed.fallbacks.items():
        if target not in known:
            raise HTTPException(
                status_code=422,
                detail=f"Fallback '{source}' -> '{target}' has no template for '{target}'",
            )


async def _validate_provider_chains(
    uow: SqlAlchemyUnitOfWork, tenant_id: TenantId, definition: dict[str, Any]
) -> None:
    """
    `provider_chains` maps a channel to saved provider ids in priority order
    (first is tried first). Every id must be one of the tenant's providers
    for that very channel, listed once.
    """
    chains = definition.get("provider_chains")
    if chains is None:
        return
    if not isinstance(chains, dict):
        raise HTTPException(status_code=422, detail="provider_chains must be an object")
    for channel_raw, ids in chains.items():
        try:
            channel = ChannelType(channel_raw)
        except ValueError:
            raise HTTPException(status_code=422, detail=f"provider_chains: unknown channel '{channel_raw}'")
        if not isinstance(ids, list) or len(set(map(str, ids))) != len(ids):
            raise HTTPException(
                status_code=422,
                detail=f"provider_chains.{channel_raw} must be a list of distinct provider ids",
            )
        try:
            wanted = [ProviderId.from_str(str(i)) for i in ids]
        except InvalidIdError as exc:
            raise HTTPException(status_code=422, detail=f"provider_chains.{channel_raw}: {exc}")
        found = {p.id: p for p in await uow.providers.list_by_ids(tenant_id, wanted)}
        for pid in wanted:
            provider = found.get(pid)
            if provider is None:
                raise HTTPException(
                    status_code=422, detail=f"provider_chains.{channel_raw}: unknown provider {pid}"
                )
            if provider.channel is not channel:
                raise HTTPException(
                    status_code=422,
                    detail=f"provider_chains.{channel_raw}: '{provider.label or provider.name}' is a {provider.channel.value} provider",
                )


@router.post("", response_model=WorkflowOut, status_code=201)
async def create_workflow(
    body: WorkflowIn,
    tenant_id: TenantId = Depends(require_scope("admin")),
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
) -> WorkflowOut:
    _validate_definition(body)
    workflow = Workflow(id=WorkflowId.new(), tenant_id=tenant_id, **body.model_dump())
    async with uow:
        await _validate_provider_chains(uow, tenant_id, body.definition)
        await uow.workflows.add(workflow)
        await uow.commit()
        return _out(await uow.workflows.get(tenant_id, workflow.id))


@router.get("", response_model=list[WorkflowOut])
async def list_workflows(
    tenant_id: TenantId = Depends(require_scope("read")),
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
) -> list[WorkflowOut]:
    async with uow:
        return [_out(w) for w in await uow.workflows.list(tenant_id)]


@router.get("/{workflow_id}", response_model=WorkflowOut)
async def get_workflow(
    workflow_id: uuid.UUID,
    tenant_id: TenantId = Depends(require_scope("read")),
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
) -> WorkflowOut:
    async with uow:
        workflow = await uow.workflows.get(tenant_id, WorkflowId(workflow_id))
    if workflow is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return _out(workflow)


@router.put("/{workflow_id}", response_model=WorkflowOut)
async def update_workflow(
    workflow_id: uuid.UUID,
    body: WorkflowIn,
    tenant_id: TenantId = Depends(require_scope("admin")),
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
) -> WorkflowOut:
    _validate_definition(body)
    async with uow:
        existing = await uow.workflows.get(tenant_id, WorkflowId(workflow_id))
        if existing is None:
            raise HTTPException(status_code=404, detail="Workflow not found")
        await _validate_provider_chains(uow, tenant_id, body.definition)
        updated = Workflow(id=existing.id, tenant_id=tenant_id, **body.model_dump())
        await uow.workflows.update(updated)
        await uow.commit()
        return _out(await uow.workflows.get(tenant_id, existing.id))


@router.delete("/{workflow_id}", status_code=204)
async def delete_workflow(
    workflow_id: uuid.UUID,
    tenant_id: TenantId = Depends(require_scope("admin")),
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
) -> None:
    async with uow:
        deleted = await uow.workflows.delete(tenant_id, WorkflowId(workflow_id))
        await uow.commit()
    if not deleted:
        raise HTTPException(status_code=404, detail="Workflow not found")
