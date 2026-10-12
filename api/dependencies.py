from __future__ import annotations

from fastapi import Cookie, Depends, Header, HTTPException, Request

from domain.value_objects.ids import InvalidIdError, TenantId
from repositories.uow import SqlAlchemyUnitOfWork
from services.identity_service import (
    AuthenticatedUser,
    AuthenticationError,
    IdentityService,
    can_access_tenant,
    is_tenant_admin,
)
from services.notification_service import NotificationService

SESSION_COOKIE = "pulse_session"


def get_uow(request: Request) -> SqlAlchemyUnitOfWork:
    return SqlAlchemyUnitOfWork(request.app.state.database.session_factory)


def get_notification_service(request: Request) -> NotificationService:
    return request.app.state.notification_service


def get_identity_service(request: Request) -> IdentityService:
    return request.app.state.identity_service


async def resolve_env_or_db_api_key(request: Request, raw_key: str) -> TenantId | None:
    """Static env-configured keys (dev bootstrap) first, then DB keys."""
    tenant_raw = request.app.state.settings.security.ingress_api_keys.get(raw_key)
    if tenant_raw is not None:
        try:
            return TenantId.from_str(tenant_raw)
        except InvalidIdError:
            raise HTTPException(status_code=500, detail="Misconfigured tenant for API key")
    return await request.app.state.identity_service.resolve_api_key(raw_key)


async def get_current_user(
    request: Request,
    pulse_session: str | None = Cookie(default=None),
) -> AuthenticatedUser:
    """Session-authenticated human. Raises 401 without a valid session."""
    if not pulse_session:
        raise HTTPException(status_code=401, detail="Not signed in")
    try:
        return await request.app.state.identity_service.resolve_session(pulse_session)
    except AuthenticationError as exc:
        raise HTTPException(status_code=401, detail=str(exc))


async def require_platform_admin(
    auth: AuthenticatedUser = Depends(get_current_user),
) -> AuthenticatedUser:
    if not auth.user.is_platform_admin:
        raise HTTPException(status_code=403, detail="Platform admin access required")
    return auth


async def require_tenant(
    request: Request,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
    pulse_session: str | None = Cookie(default=None),
) -> TenantId:
    """
    Resolves the tenant a request acts on. Two paths:
      - machine ingress: X-API-Key (env-configured or DB-issued key);
      - console users: session cookie + X-Tenant-Id, checked against the
        user's memberships (platform admins may act on any tenant).
    """
    if x_api_key:
        tenant_id = await resolve_env_or_db_api_key(request, x_api_key)
        if tenant_id is None:
            raise HTTPException(status_code=401, detail="Invalid API key")
        return tenant_id

    if pulse_session:
        auth = await get_current_user(request, pulse_session)
        if not x_tenant_id:
            raise HTTPException(status_code=400, detail="X-Tenant-Id header required")
        try:
            tenant_id = TenantId.from_str(x_tenant_id)
        except InvalidIdError:
            raise HTTPException(status_code=400, detail="Invalid X-Tenant-Id")
        if not can_access_tenant(auth, tenant_id):
            raise HTTPException(status_code=403, detail="Not a member of this tenant")
        return tenant_id

    raise HTTPException(status_code=401, detail="Provide an API key or sign in")


async def require_tenant_admin(
    request: Request,
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
    auth: AuthenticatedUser = Depends(get_current_user),
) -> TenantId:
    """Session-only: tenant admin (or platform admin) acting on X-Tenant-Id."""
    if not x_tenant_id:
        raise HTTPException(status_code=400, detail="X-Tenant-Id header required")
    try:
        tenant_id = TenantId.from_str(x_tenant_id)
    except InvalidIdError:
        raise HTTPException(status_code=400, detail="Invalid X-Tenant-Id")
    if not is_tenant_admin(auth, tenant_id):
        raise HTTPException(status_code=403, detail="Tenant admin access required")
    return tenant_id


TenantDep = Depends(require_tenant)
