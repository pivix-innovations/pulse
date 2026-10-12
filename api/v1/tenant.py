"""Tenant-admin endpoints: members and API keys for the acting tenant."""
from __future__ import annotations

import secrets
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.dependencies import get_identity_service, get_uow, require_tenant_admin
from domain.entities.identity import Membership, User
from domain.enums.identity import TenantRole
from domain.value_objects.ids import ApiKeyId, MembershipId, TenantId, UserId
from repositories.uow import SqlAlchemyUnitOfWork
from services.identity_service import IdentityService, hash_password

router = APIRouter(prefix="/tenant", tags=["tenant"])

_EMAIL = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


# ── Members ───────────────────────────────────────────────────────────

class MemberIn(BaseModel):
    email: str = Field(min_length=3, max_length=320, pattern=_EMAIL)
    role: TenantRole = TenantRole.MEMBER
    full_name: str = Field(default="", max_length=200)


class MemberOut(BaseModel):
    membership_id: uuid.UUID
    user_id: uuid.UUID
    email: str
    full_name: str
    role: TenantRole
    is_active: bool
    temporary_password: str | None = None  # only when the user was just created


@router.get("/members", response_model=list[MemberOut])
async def list_members(
    tenant_id: TenantId = Depends(require_tenant_admin),
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
) -> list[MemberOut]:
    async with uow:
        out = []
        for m in await uow.memberships.list_for_tenant(tenant_id):
            user = await uow.users.get(m.user_id)
            if user is None:
                continue
            out.append(MemberOut(
                membership_id=m.id.value, user_id=user.id.value, email=user.email,
                full_name=user.full_name, role=m.role, is_active=user.is_active,
            ))
        return out


@router.post("/members", response_model=MemberOut, status_code=201)
async def add_member(
    body: MemberIn,
    tenant_id: TenantId = Depends(require_tenant_admin),
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
) -> MemberOut:
    """
    Attach a user to this tenant, creating the account if the email is
    new — the generated temporary password is returned exactly once.
    """
    email = body.email.strip().lower()
    temp = None
    async with uow:
        user = await uow.users.get_by_email(email)
        if user is None:
            temp = secrets.token_urlsafe(12)
            user = User(
                id=UserId.new(), email=email,
                password_hash=hash_password(temp), full_name=body.full_name,
            )
            await uow.users.add(user)
        membership = Membership(
            id=MembershipId.new(), tenant_id=tenant_id, user_id=user.id, role=body.role
        )
        await uow.memberships.upsert(membership)
        await uow.commit()
        stored = await uow.memberships.get(tenant_id, user.id)
        return MemberOut(
            membership_id=stored.id.value, user_id=user.id.value, email=user.email,
            full_name=user.full_name, role=stored.role, is_active=user.is_active,
            temporary_password=temp,
        )


@router.delete("/members/{membership_id}", status_code=204)
async def remove_member(
    membership_id: uuid.UUID,
    tenant_id: TenantId = Depends(require_tenant_admin),
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
) -> None:
    async with uow:
        memberships = await uow.memberships.list_for_tenant(tenant_id)
        if not any(m.id.value == membership_id for m in memberships):
            raise HTTPException(status_code=404, detail="Membership not found")
        await uow.memberships.delete(MembershipId(membership_id))
        await uow.commit()


# ── API keys ──────────────────────────────────────────────────────────

class ApiKeyIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class ApiKeyOut(BaseModel):
    id: uuid.UUID
    name: str
    prefix: str
    is_active: bool
    created_at: datetime | None
    key: str | None = None  # plaintext, present only in the create response


@router.get("/api-keys", response_model=list[ApiKeyOut])
async def list_api_keys(
    tenant_id: TenantId = Depends(require_tenant_admin),
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
) -> list[ApiKeyOut]:
    async with uow:
        return [
            ApiKeyOut(
                id=k.id.value, name=k.name, prefix=k.prefix,
                is_active=k.is_active, created_at=k.created_at,
            )
            for k in await uow.api_keys.list_for_tenant(tenant_id)
        ]


@router.post("/api-keys", response_model=ApiKeyOut, status_code=201)
async def create_api_key(
    body: ApiKeyIn,
    tenant_id: TenantId = Depends(require_tenant_admin),
    identity: IdentityService = Depends(get_identity_service),
) -> ApiKeyOut:
    token, key = await identity.create_api_key(tenant_id, body.name)
    return ApiKeyOut(
        id=key.id.value, name=key.name, prefix=key.prefix,
        is_active=True, created_at=key.created_at, key=token,
    )


@router.delete("/api-keys/{key_id}", status_code=204)
async def revoke_api_key(
    key_id: uuid.UUID,
    tenant_id: TenantId = Depends(require_tenant_admin),
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
) -> None:
    async with uow:
        if not await uow.api_keys.revoke(tenant_id, ApiKeyId(key_id)):
            raise HTTPException(status_code=404, detail="API key not found")
        await uow.commit()
