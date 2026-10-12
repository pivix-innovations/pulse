"""Platform-admin endpoints: manage tenants and users across the platform."""
from __future__ import annotations

import secrets
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.dependencies import get_uow, require_platform_admin
from domain.entities.identity import Tenant, User
from domain.value_objects.ids import TenantId, UserId
from repositories.uow import SqlAlchemyUnitOfWork
from services.identity_service import AuthenticatedUser, hash_password

router = APIRouter(
    prefix="/admin", tags=["admin"], dependencies=[Depends(require_platform_admin)]
)

_EMAIL = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


# ── Tenants ───────────────────────────────────────────────────────────

class TenantIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    slug: str = Field(min_length=1, max_length=100, pattern=r"^[a-z0-9][a-z0-9-]*$")


class TenantPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    is_active: bool | None = None


class TenantOut(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    is_active: bool
    created_at: datetime | None


def _tenant_out(t: Tenant) -> TenantOut:
    return TenantOut(
        id=t.id.value, name=t.name, slug=t.slug, is_active=t.is_active, created_at=t.created_at
    )


@router.get("/tenants", response_model=list[TenantOut])
async def list_tenants(uow: SqlAlchemyUnitOfWork = Depends(get_uow)) -> list[TenantOut]:
    async with uow:
        return [_tenant_out(t) for t in await uow.tenants.list()]


@router.post("/tenants", response_model=TenantOut, status_code=201)
async def create_tenant(
    body: TenantIn, uow: SqlAlchemyUnitOfWork = Depends(get_uow)
) -> TenantOut:
    tenant = Tenant(id=TenantId.new(), name=body.name, slug=body.slug)
    async with uow:
        await uow.tenants.add(tenant)
        await uow.commit()
        return _tenant_out(await uow.tenants.get(tenant.id))


@router.patch("/tenants/{tenant_id}", response_model=TenantOut)
async def update_tenant(
    tenant_id: uuid.UUID, body: TenantPatch, uow: SqlAlchemyUnitOfWork = Depends(get_uow)
) -> TenantOut:
    async with uow:
        tenant = await uow.tenants.get(TenantId(tenant_id))
        if tenant is None:
            raise HTTPException(status_code=404, detail="Tenant not found")
        if body.name is not None:
            tenant.name = body.name
        if body.is_active is not None:
            tenant.is_active = body.is_active
        await uow.tenants.update(tenant)
        await uow.commit()
        return _tenant_out(await uow.tenants.get(tenant.id))


# ── Users ─────────────────────────────────────────────────────────────

class UserIn(BaseModel):
    email: str = Field(min_length=3, max_length=320, pattern=_EMAIL)
    full_name: str = Field(default="", max_length=200)
    is_platform_admin: bool = False
    # Omit to have a temporary password generated and returned once.
    password: str | None = Field(default=None, min_length=8, max_length=200)


class UserPatch(BaseModel):
    full_name: str | None = Field(default=None, max_length=200)
    is_platform_admin: bool | None = None
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=8, max_length=200)


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str
    is_platform_admin: bool
    is_active: bool
    created_at: datetime | None
    temporary_password: str | None = None  # only on create, when generated


def _user_out(u: User, temp: str | None = None) -> UserOut:
    return UserOut(
        id=u.id.value, email=u.email, full_name=u.full_name,
        is_platform_admin=u.is_platform_admin, is_active=u.is_active,
        created_at=u.created_at, temporary_password=temp,
    )


@router.get("/users", response_model=list[UserOut])
async def list_users(uow: SqlAlchemyUnitOfWork = Depends(get_uow)) -> list[UserOut]:
    async with uow:
        return [_user_out(u) for u in await uow.users.list()]


@router.post("/users", response_model=UserOut, status_code=201)
async def create_user(
    body: UserIn, uow: SqlAlchemyUnitOfWork = Depends(get_uow)
) -> UserOut:
    temp = None
    password = body.password
    if password is None:
        password = temp = secrets.token_urlsafe(12)
    user = User(
        id=UserId.new(),
        email=body.email.strip().lower(),
        password_hash=hash_password(password),
        full_name=body.full_name,
        is_platform_admin=body.is_platform_admin,
    )
    async with uow:
        await uow.users.add(user)
        await uow.commit()
        return _user_out(await uow.users.get(user.id), temp)


@router.patch("/users/{user_id}", response_model=UserOut)
async def update_user(
    user_id: uuid.UUID,
    body: UserPatch,
    auth: AuthenticatedUser = Depends(require_platform_admin),
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
) -> UserOut:
    async with uow:
        user = await uow.users.get(UserId(user_id))
        if user is None:
            raise HTTPException(status_code=404, detail="User not found")
        demoting = body.is_platform_admin is False or body.is_active is False
        if demoting and user.id == auth.user.id:
            raise HTTPException(status_code=409, detail="You cannot demote or disable yourself")
        if body.full_name is not None:
            user.full_name = body.full_name
        if body.is_platform_admin is not None:
            user.is_platform_admin = body.is_platform_admin
        if body.is_active is not None:
            user.is_active = body.is_active
        if body.password is not None:
            user.password_hash = hash_password(body.password)
        await uow.users.update(user)
        await uow.commit()
        return _user_out(await uow.users.get(user.id))
