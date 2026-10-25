from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from api.dependencies import get_uow, require_scope
from domain.entities.audit import AuditEntry
from domain.value_objects.ids import TenantId
from repositories.uow import SqlAlchemyUnitOfWork

router = APIRouter(prefix="/audit-log", tags=["audit"])


class AuditEntryOut(BaseModel):
    id: uuid.UUID
    actor_type: str
    actor_id: str | None
    actor_label: str | None
    action: str
    resource_type: str
    resource_id: str | None
    detail: dict[str, Any]
    created_at: datetime | None


def _out(e: AuditEntry) -> AuditEntryOut:
    return AuditEntryOut(
        id=e.id.value, actor_type=e.actor_type, actor_id=e.actor_id, actor_label=e.actor_label, action=e.action,
        resource_type=e.resource_type, resource_id=e.resource_id, detail=e.detail, created_at=e.created_at,
    )


@router.get("", response_model=list[AuditEntryOut])
async def list_audit_log(
    resource_type: str | None = Query(default=None, max_length=40),
    resource_id: str | None = Query(default=None, max_length=64),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    tenant_id: TenantId = Depends(require_scope("admin")),
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
) -> list[AuditEntryOut]:
    """Who changed workflows, templates, providers, API keys, members and suppressions, newest first."""
    async with uow:
        rows = await uow.audit.list(
            tenant_id, resource_type=resource_type, resource_id=resource_id, limit=limit, offset=offset
        )
    return [_out(r) for r in rows]
