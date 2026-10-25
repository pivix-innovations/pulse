"""Records successful changes to configuration (workflows, templates, providers, API keys, members, suppressions)."""
from __future__ import annotations

import inspect
import json
import logging
import re

from fastapi import Request

from api.dependencies import get_uow
from domain.entities.audit import AuditEntry
from domain.value_objects.ids import AuditEntryId

logger = logging.getLogger(__name__)

# First path segment(s) after /api/v1 -> resource type
_RESOURCES = {
    "workflows": "workflow",
    "templates": "template",
    "providers": "provider",
    "suppressions": "suppression",
    "tenant/api-keys": "api_key",
    "tenant/members": "member",
}
_SKIP_VERBS = {"preview", "test"}
_TEMPLATE = re.compile(r"^/api/v1/(?P<res>[a-z-]+(?:/(?:api-keys|members))?)(?P<rest>(?:/\{[a-z_]+\})?(?:/[a-z-]+)?)$")


def classify(method: str, route_path: str) -> tuple[str, str] | None:
    """(resource_type, action) for a mutating route template, or None when it is not audited."""
    if method not in ("POST", "PUT", "DELETE"):
        return None
    m = _TEMPLATE.match(route_path)
    if not m:
        return None
    # "tenant/api-keys" must win over a bare "tenant" match
    resource = _RESOURCES.get(m["res"])
    if resource is None:
        return None
    rest = m["rest"]
    has_id = "{" in rest
    verb = rest.rsplit("/", 1)[-1] if rest and not rest.rsplit("/", 1)[-1].startswith("{") else None
    if verb in _SKIP_VERBS or (method == "POST" and not has_id and rest and verb):   # e.g. /templates/preview
        return None
    if verb:
        return resource, f"{resource}.{verb.replace('-', '_')}"
    action = {"POST": "create", "PUT": "update", "DELETE": "delete"}[method]
    return resource, f"{resource}.{action}"


async def audit_middleware(request: Request, call_next):
    response = await call_next(request)
    try:
        if not 200 <= response.status_code < 300:
            return response
        # route.path omits the include prefix on some FastAPI versions, so rebuild the template from the URL.
        template = request.url.path
        for name, value in request.path_params.items():
            template = template.replace(f"/{value}", f"/{{{name}}}")
        kind = classify(request.method, template)
        tenant_id = getattr(request.state, "audit_tenant_id", None)
        if kind is None or tenant_id is None:
            return response
        resource_type, action = kind
        resource_id = next(
            (str(v) for k, v in request.path_params.items() if k.endswith("_id")), None
        )
        if resource_id is None and request.method == "POST":
            # Creates: the new id is in the response body. Buffer it (it is small) and hand it back.
            body = b"".join([chunk async for chunk in response.body_iterator])
            from starlette.responses import Response

            headers = {k: v for k, v in response.headers.items() if k.lower() != "content-length"}
            response = Response(body, response.status_code, headers, response.media_type)
            try:
                resource_id = str(json.loads(body).get("id") or "") or None
            except (ValueError, AttributeError):
                resource_id = None
        actor_type, actor_id, actor_label = getattr(request.state, "actor", ("unknown", None, None))
        get = request.app.dependency_overrides.get(get_uow, get_uow)
        async with (get(request) if inspect.signature(get).parameters else get()) as uow:
            await uow.audit.add(AuditEntry(
                id=AuditEntryId.new(), tenant_id=tenant_id, actor_type=actor_type, actor_id=actor_id,
                actor_label=actor_label, action=action, resource_type=resource_type, resource_id=resource_id,
                detail={"method": request.method, "status": response.status_code},
            ))
            await uow.commit()
    except Exception:   # an audit failure must never fail the change that already happened
        logger.exception("Could not write audit entry")
    return response
