from __future__ import annotations

import uuid

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from api.dependencies import SESSION_COOKIE, get_current_user, get_identity_service, get_uow
from domain.enums.identity import TenantRole
from repositories.uow import SqlAlchemyUnitOfWork
from services.identity_service import (
    AuthenticatedUser,
    AuthenticationError,
    IdentityService,
    hash_password,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["auth"])

SESSION_MAX_AGE = 7 * 24 * 3600


class LoginIn(BaseModel):
    email: str = Field(min_length=3, max_length=320, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    password: str = Field(min_length=1)


class MembershipOut(BaseModel):
    tenant_id: uuid.UUID
    tenant_name: str
    tenant_slug: str
    role: TenantRole


class MeOut(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str
    is_platform_admin: bool
    memberships: list[MembershipOut]


class ChangePasswordIn(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=200)


async def _me(auth: AuthenticatedUser, uow: SqlAlchemyUnitOfWork) -> MeOut:
    memberships: list[MembershipOut] = []
    async with uow:
        for m in auth.memberships:
            tenant = await uow.tenants.get(m.tenant_id)
            if tenant is None or not tenant.is_active:
                continue
            memberships.append(MembershipOut(
                tenant_id=m.tenant_id.value, tenant_name=tenant.name,
                tenant_slug=tenant.slug, role=m.role,
            ))
        # Platform admins can act on every active tenant.
        if auth.user.is_platform_admin:
            seen = {m.tenant_id for m in memberships}
            for tenant in await uow.tenants.list():
                if tenant.is_active and tenant.id.value not in seen:
                    memberships.append(MembershipOut(
                        tenant_id=tenant.id.value, tenant_name=tenant.name,
                        tenant_slug=tenant.slug, role=TenantRole.ADMIN,
                    ))
    return MeOut(
        id=auth.user.id.value, email=auth.user.email, full_name=auth.user.full_name,
        is_platform_admin=auth.user.is_platform_admin, memberships=memberships,
    )


@router.post("/login", response_model=MeOut)
async def login(
    body: LoginIn,
    request: Request,
    response: Response,
    identity: IdentityService = Depends(get_identity_service),
) -> MeOut:
    try:
        token, auth = await identity.login(body.email, body.password)
    except AuthenticationError as exc:
        raise HTTPException(status_code=401, detail=str(exc))
    response.set_cookie(
        SESSION_COOKIE, token,
        max_age=SESSION_MAX_AGE, httponly=True, samesite="lax",
        secure=request.url.scheme == "https",
        path="/",
    )
    return await _me(auth, get_uow(request))


@router.post("/logout", status_code=204)
async def logout(
    response: Response,
    pulse_session: str | None = Cookie(default=None),
    identity: IdentityService = Depends(get_identity_service),
) -> None:
    if pulse_session:
        await identity.logout(pulse_session)
    response.delete_cookie(SESSION_COOKIE, path="/")


@router.get("/me", response_model=MeOut)
async def me(
    request: Request,
    auth: AuthenticatedUser = Depends(get_current_user),
) -> MeOut:
    return await _me(auth, get_uow(request))


@router.post("/password", status_code=204)
async def change_password(
    body: ChangePasswordIn,
    auth: AuthenticatedUser = Depends(get_current_user),
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
) -> None:
    if not verify_password(body.current_password, auth.user.password_hash):
        raise HTTPException(status_code=403, detail="Current password is incorrect")
    async with uow:
        user = await uow.users.get(auth.user.id)
        user.password_hash = hash_password(body.new_password)
        await uow.users.update(user)
        await uow.commit()
