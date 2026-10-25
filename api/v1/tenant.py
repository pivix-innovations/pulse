"""Tenant-admin endpoints: members and API keys for the acting tenant."""
from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from api.dependencies import get_identity_service, get_uow, require_tenant_admin, require_scope
from domain.entities.identity import Membership, User
from domain.enums.identity import TenantRole
from domain.value_objects.ids import ApiKeyId, MembershipId, TenantId, UserId
from repositories.uow import SqlAlchemyUnitOfWork
from domain.value_objects.scopes import Scope
from services.identity_service import ApiKeyError, IdentityService, hash_password

router = APIRouter(prefix="/tenant", tags=["tenant"])

_EMAIL = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


# ── Usage ─────────────────────────────────────────────────────────────

class UsageOut(BaseModel):
    rate_limit_per_second: float | None
    rate_burst: int | None
    daily_quota: int | None            # None = unlimited
    used_today: int
    quota_remaining: int | None
    enforced: bool


@router.get("/usage", response_model=UsageOut)
async def usage(request: Request, tenant_id: TenantId = Depends(require_scope("read"))) -> UsageOut:
    """Event ingress limits in force for this tenant and today's usage (UTC day)."""
    service = getattr(request.app.state, "rate_limit_service", None)
    if service is None or not service.enabled:
        return UsageOut(rate_limit_per_second=None, rate_burst=None, daily_quota=None,
                        used_today=0, quota_remaining=None, enforced=False)
    limits, used = await service.usage(tenant_id)
    return UsageOut(
        rate_limit_per_second=limits.rate_per_second, rate_burst=limits.burst,
        daily_quota=limits.daily_quota, used_today=used,
        quota_remaining=None if limits.daily_quota is None else max(0, limits.daily_quota - used),
        enforced=True,
    )


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
    # Least privilege by default: a key that can only send events.
    scopes: list[Scope] = Field(default_factory=lambda: [Scope.INGEST], min_length=1)
    expires_in_days: int | None = Field(default=None, ge=1, le=3650)


class ApiKeyOut(BaseModel):
    id: uuid.UUID
    name: str
    prefix: str
    scopes: list[str]
    status: str                       # active | expired | revoked
    expires_at: datetime | None
    last_used_at: datetime | None
    rotated_from: uuid.UUID | None    # the key this one replaced
    replaced_by: uuid.UUID | None     # the key that replaced this one (it is winding down)
    is_active: bool
    created_at: datetime | None
    key: str | None = None            # plaintext, present only in the create / rotate response


class RotateIn(BaseModel):
    # How long the old key keeps working so clients can switch without downtime; 0 = revoke now.
    grace_hours: int = Field(default=24, ge=0, le=24 * 30)


class RotateOut(ApiKeyOut):
    previous_key_valid_until: datetime


def _status(key, now) -> str:
    if not key.is_active:
        return "revoked"
    return "expired" if key.expires_at is not None and key.expires_at <= now else "active"


def _key_out(key, now, *, replaced_by=None, plaintext: str | None = None) -> dict:
    return dict(
        id=key.id.value, name=key.name, prefix=key.prefix, scopes=list(key.scopes),
        status=_status(key, now), expires_at=key.expires_at, last_used_at=key.last_used_at,
        rotated_from=key.rotated_from_id.value if key.rotated_from_id else None,
        replaced_by=replaced_by, is_active=key.is_active, created_at=key.created_at, key=plaintext,
    )


@router.get("/api-keys", response_model=list[ApiKeyOut])
async def list_api_keys(
    tenant_id: TenantId = Depends(require_tenant_admin),
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
) -> list[ApiKeyOut]:
    now = datetime.now(timezone.utc)
    async with uow:
        keys = await uow.api_keys.list_for_tenant(tenant_id)
    successor = {k.rotated_from_id.value: k.id.value for k in keys if k.rotated_from_id}
    return [ApiKeyOut(**_key_out(k, now, replaced_by=successor.get(k.id.value))) for k in keys]


@router.post("/api-keys", response_model=ApiKeyOut, status_code=201)
async def create_api_key(
    body: ApiKeyIn,
    tenant_id: TenantId = Depends(require_tenant_admin),
    identity: IdentityService = Depends(get_identity_service),
) -> ApiKeyOut:
    now = datetime.now(timezone.utc)
    try:
        token, key = await identity.create_api_key(
            tenant_id, body.name, scopes=[s.value for s in body.scopes],
            expires_at=now + timedelta(days=body.expires_in_days) if body.expires_in_days else None,
        )
    except ApiKeyError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return ApiKeyOut(**_key_out(key, now, plaintext=token))


@router.post("/api-keys/{key_id}/rotate", response_model=RotateOut, status_code=201)
async def rotate_api_key(
    key_id: uuid.UUID,
    body: RotateIn | None = None,
    tenant_id: TenantId = Depends(require_tenant_admin),
    identity: IdentityService = Depends(get_identity_service),
) -> RotateOut:
    """
    Issue a replacement key with the same scopes and keep the old one alive for
    the grace period, so deployed clients can be updated without an outage.
    """
    now = datetime.now(timezone.utc)
    grace = timedelta(hours=(body or RotateIn()).grace_hours)
    try:
        token, new, old_valid_until = await identity.rotate_api_key(tenant_id, ApiKeyId(key_id), grace=grace)
    except LookupError:
        raise HTTPException(status_code=404, detail="API key not found")
    except ApiKeyError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return RotateOut(**_key_out(new, now, plaintext=token), previous_key_valid_until=old_valid_until)


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
