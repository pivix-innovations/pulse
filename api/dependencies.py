from __future__ import annotations

from fastapi import Depends, Header, HTTPException, Request

from domain.value_objects.ids import InvalidIdError, TenantId
from repositories.uow import SqlAlchemyUnitOfWork
from services.notification_service import NotificationService


def require_tenant(
    request: Request,
    x_api_key: str = Header(..., alias="X-API-Key"),
) -> TenantId:
    """Authenticates the caller by API key and resolves their tenant."""
    tenant_raw = request.app.state.settings.security.ingress_api_keys.get(x_api_key)
    if tenant_raw is None:
        raise HTTPException(status_code=401, detail="Invalid API key")
    try:
        return TenantId.from_str(tenant_raw)
    except InvalidIdError:
        raise HTTPException(status_code=500, detail="Misconfigured tenant for API key")


def get_uow(request: Request) -> SqlAlchemyUnitOfWork:
    return SqlAlchemyUnitOfWork(request.app.state.database.session_factory)


def get_notification_service(request: Request) -> NotificationService:
    return request.app.state.notification_service


TenantDep = Depends(require_tenant)
