from __future__ import annotations

from fastapi import Cookie, Depends, Header, HTTPException, Request

from domain.value_objects.ids import InvalidIdError, TenantId
from domain.value_objects.scopes import ALL_SCOPES, satisfies
from repositories.uow import SqlAlchemyUnitOfWork
from services.secrets import SecretBox
from services.identity_service import (
    ApiPrincipal,
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


async def resolve_env_or_db_api_key(request: Request, raw_key: str) -> ApiPrincipal | None:
    """Static env-configured keys (dev bootstrap; all scopes) first, then DB keys."""
    tenant_raw = request.app.state.settings.security.ingress_api_keys.get(raw_key)
    if tenant_raw is not None:
        try:
            return ApiPrincipal(TenantId.from_str(tenant_raw), frozenset(ALL_SCOPES))
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
        auth = await request.app.state.identity_service.resolve_session(pulse_session)
        request.state.actor = ("user", str(auth.user.id), auth.user.email)
        return auth
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
        principal = await resolve_env_or_db_api_key(request, x_api_key)
        if principal is None:
            raise HTTPException(status_code=401, detail="Invalid API key")
        request.state.scopes = principal.scopes     # what this key may do; sessions leave it unset
        request.state.actor = (
            ("api_key", str(principal.key_id), None) if principal.key_id else ("env_key", None, None)
        )
        request.state.audit_tenant_id = principal.tenant_id
        return principal.tenant_id

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
        request.state.audit_tenant_id = tenant_id
        return tenant_id

    raise HTTPException(status_code=401, detail="Provide an API key or sign in")


def require_scope(*accepted: str):
    """
    Dependency: the caller is a tenant member / API key *and*, if it is an API
    key, holds one of `accepted` scopes (`admin` satisfies any). Console
    sessions are governed by their role instead and pass.
    """

    async def dependency(
        request: Request, tenant_id: TenantId = Depends(require_tenant)
    ) -> TenantId:
        scopes = getattr(request.state, "scopes", None)
        if scopes is not None and not satisfies(scopes, accepted):
            raise HTTPException(
                status_code=403,
                detail=f"This API key lacks the '{accepted[0]}' scope"
                + (f" (or one of: {', '.join(accepted[1:])})" if len(accepted) > 1 else ""),
            )
        return tenant_id

    dependency.__name__ = "require_scope"
    dependency.scopes = accepted  # type: ignore[attr-defined]
    return dependency


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
    request.state.audit_tenant_id = tenant_id
    return tenant_id


TenantDep = Depends(require_tenant)


def get_secret_box(request: Request) -> SecretBox:
    box = getattr(request.app.state, "secret_box", None)
    if box is None:
        box = SecretBox(request.app.state.settings.security.secrets_key)
        request.app.state.secret_box = box
    return box
