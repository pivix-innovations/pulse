from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from api.dependencies import get_secret_box, get_uow, require_tenant
from domain.entities.provider import Provider
from domain.enums import ChannelType
from domain.value_objects.ids import ProviderId, TenantId
from providers.receipts import supports_receipts, webhook_path, webhook_url
from providers.catalog import ProviderSpec, get_spec, list_specs
from repositories.uow import SqlAlchemyUnitOfWork
from services.provider_health import assess
from services.secrets import SecretBox, SecretsDecryptionError, SecretsNotConfigured

router = APIRouter(prefix="/providers", tags=["providers"])
catalog_router = APIRouter(prefix="/provider-catalog", tags=["providers"])


# ── Catalog ────────────────────────────────────────────────────────────


@catalog_router.get("")
async def list_catalog(
    channel: ChannelType | None = None, _: TenantId = Depends(require_tenant)
) -> list[dict[str, Any]]:
    """Providers a client can choose from: name, logo and the fields to fill in."""
    return [s.public() for s in list_specs() if channel is None or s.channel is channel]


@catalog_router.get("/{channel}/{key}/logo")
async def catalog_logo(channel: ChannelType, key: str) -> Response:
    spec = get_spec(channel, key)
    if spec is None:
        raise HTTPException(status_code=404, detail="Unknown provider")
    return PlainTextResponse(
        spec.logo_svg(),
        media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=86400"},
    )


class TestIn(BaseModel):
    config: dict[str, Any] = Field(default_factory=dict)
    secrets: dict[str, Any | None] = Field(default_factory=dict)
    # Edit forms leave saved secrets blank; name the saved provider to reuse them.
    provider_id: uuid.UUID | None = None


class TestOut(BaseModel):
    ok: bool
    detail: str


@catalog_router.post("/{channel}/{key}/test", response_model=TestOut)
async def test_connection(
    channel: ChannelType,
    key: str,
    body: TestIn,
    tenant_id: TenantId = Depends(require_tenant),
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
    box: SecretBox = Depends(get_secret_box),
) -> TestOut:
    """Check credentials against the provider before (or after) saving them."""
    spec = _spec_or_422(channel, key)
    if spec.check is None:
        raise HTTPException(status_code=422, detail=f"{spec.name} has no connection test")
    stored: dict[str, Any] = {}
    if body.provider_id is not None:
        async with uow:
            existing = await uow.providers.get(tenant_id, ProviderId(body.provider_id))
        if existing is None or (existing.channel, existing.name) != (channel, key):
            raise HTTPException(status_code=404, detail="Provider not found")
        try:
            stored = box.decrypt(existing.secrets_encrypted)
        except (SecretsNotConfigured, SecretsDecryptionError):
            stored = {}
    merged = {**stored, **{k: v for k, v in body.secrets.items() if v not in (None, "")}}
    try:
        config, secrets = spec.validate(body.config, merged)
    except ValueError as exc:
        return TestOut(ok=False, detail=str(exc))
    ok, detail = await spec.check(config, secrets)
    return TestOut(ok=ok, detail=detail)


# ── Saved providers ────────────────────────────────────────────────────


class ProviderIn(BaseModel):
    channel: ChannelType
    # Catalog key (see GET /provider-catalog), e.g. "smtp", "fcm".
    name: str = Field(min_length=1, max_length=100)
    # The client's own name for this saved provider; defaults to the catalog name.
    label: str | None = Field(default=None, max_length=200)
    config: dict[str, Any] = Field(default_factory=dict)
    # Write-only. On update, keys given replace the stored value, omitted
    # keys are kept, and a null value removes the key.
    secrets: dict[str, Any | None] = Field(default_factory=dict)
    priority: int = 0
    is_active: bool = True


class ProviderOut(BaseModel):
    id: uuid.UUID
    channel: ChannelType
    name: str
    label: str
    config: dict[str, Any]
    # Names of the secrets that are set; their values are never returned.
    secrets_set: list[str]
    priority: int
    is_active: bool
    catalog: dict[str, Any] | None
    # Where this provider should send delivery receipts, when it supports them.
    webhook_path: str | None = None
    webhook_url: str | None = None
    used_by: list[dict[str, Any]] = Field(default_factory=list)
    created_at: datetime | None
    updated_at: datetime | None


def get_public_url(request: Request) -> str | None:
    return request.app.state.settings.api.public_url


def _stored_secrets(box: SecretBox, p: Provider) -> dict[str, Any]:
    try:
        return box.decrypt(p.secrets_encrypted)
    except (SecretsNotConfigured, SecretsDecryptionError):
        return {}


def _out(
    p: Provider,
    box: SecretBox,
    used_by: list[dict[str, Any]] | None = None,
    public_url: str | None = None,
) -> ProviderOut:
    spec = get_spec(p.channel, p.name)
    return ProviderOut(
        id=p.id.value,
        channel=p.channel,
        name=p.name,
        label=p.label or p.name,
        config=p.config,
        secrets_set=sorted(_stored_secrets(box, p)),
        priority=p.priority,
        is_active=p.is_active,
        catalog=(
            {"name": spec.name, "logo_url": spec.public()["logo_url"], "builtin": spec.builtin}
            if spec
            else None
        ),
        webhook_path=webhook_path(p.id) if spec and supports_receipts(spec.id) else None,
        webhook_url=(
            webhook_url(public_url, p.id) if spec and supports_receipts(spec.id) else None
        ),
        used_by=used_by or [],
        created_at=p.created_at,
        updated_at=p.updated_at,
    )


def _spec_or_422(channel: ChannelType, name: str) -> ProviderSpec:
    spec = get_spec(channel, name)
    if spec is None:
        raise HTTPException(
            status_code=422,
            detail=f"'{name}' is not a {channel.value} provider; see GET /provider-catalog",
        )
    return spec


def _workflow_usage(workflows: list, provider_id: ProviderId | None = None) -> dict[str, list]:
    """provider id -> [{id, name, channel}] for every workflow chain naming it."""
    usage: dict[str, list] = {}
    for w in workflows:
        for channel, ids in (w.definition.get("provider_chains") or {}).items():
            for pid in ids:
                usage.setdefault(str(pid), []).append(
                    {"id": str(w.id), "name": w.name, "channel": channel}
                )
    return usage


def _encrypt_or_503(box: SecretBox, secrets: dict[str, Any]) -> str | None:
    try:
        return box.encrypt(secrets)
    except SecretsNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@router.post("", response_model=ProviderOut, status_code=201)
async def create_provider(
    body: ProviderIn,
    tenant_id: TenantId = Depends(require_tenant),
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
    box: SecretBox = Depends(get_secret_box),
    public_url: str | None = Depends(get_public_url),
) -> ProviderOut:
    spec = _spec_or_422(body.channel, body.name)
    try:
        config, secrets = spec.validate(
            body.config, {k: v for k, v in body.secrets.items() if v not in (None, "")}
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    provider = Provider(
        id=ProviderId.new(),
        tenant_id=tenant_id,
        channel=body.channel,
        name=body.name,
        label=body.label or spec.name,
        config=config,
        secrets_encrypted=_encrypt_or_503(box, secrets),
        priority=body.priority,
        is_active=body.is_active,
    )
    async with uow:
        await uow.providers.add(provider)
        await uow.commit()
        return _out(await uow.providers.get(tenant_id, provider.id), box, public_url=public_url)


@router.get("", response_model=list[ProviderOut])
async def list_providers(
    channel: ChannelType | None = None,
    tenant_id: TenantId = Depends(require_tenant),
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
    box: SecretBox = Depends(get_secret_box),
    public_url: str | None = Depends(get_public_url),
) -> list[ProviderOut]:
    async with uow:
        providers = await uow.providers.list(tenant_id)
        usage = _workflow_usage(await uow.workflows.list(tenant_id))
    return [
        _out(p, box, usage.get(str(p.id)), public_url)
        for p in providers
        if channel is None or p.channel is channel
    ]


class ProviderHealthOut(BaseModel):
    provider_id: uuid.UUID
    status: str  # healthy | degraded | down | idle
    window_hours: int
    calls: int
    failures: int
    success_rate: float | None
    avg_latency_ms: float | None
    p95_latency_ms: float | None
    last_success_at: datetime | None
    last_failure_at: datetime | None
    last_error: str | None


@router.get("/health", response_model=list[ProviderHealthOut])
async def providers_health(
    window_hours: int = Query(default=24, ge=1, le=24 * 30),
    tenant_id: TenantId = Depends(require_tenant),
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
) -> list[ProviderHealthOut]:
    """
    Live health of each saved provider from its recent calls: success rate,
    latency and the last failure. Recipient errors (bad numbers, refused
    addresses) are not held against a provider.
    """
    since = datetime.now(timezone.utc) - timedelta(hours=window_hours)
    async with uow:
        providers = await uow.providers.list(tenant_id)
        stats = await uow.provider_calls.stats(tenant_id, since)
    out = []
    for p in providers:
        h = assess(stats.get(p.id))
        out.append(ProviderHealthOut(provider_id=p.id.value, window_hours=window_hours, **{
            k: getattr(h, k) for k in (
                "status", "calls", "failures", "success_rate", "avg_latency_ms",
                "p95_latency_ms", "last_success_at", "last_failure_at", "last_error",
            )
        }))
    return out


@router.get("/{provider_id}", response_model=ProviderOut)
async def get_provider(
    provider_id: uuid.UUID,
    tenant_id: TenantId = Depends(require_tenant),
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
    box: SecretBox = Depends(get_secret_box),
    public_url: str | None = Depends(get_public_url),
) -> ProviderOut:
    async with uow:
        provider = await uow.providers.get(tenant_id, ProviderId(provider_id))
        usage = _workflow_usage(await uow.workflows.list(tenant_id))
    if provider is None:
        raise HTTPException(status_code=404, detail="Provider not found")
    return _out(provider, box, usage.get(str(provider.id)), public_url)


@router.put("/{provider_id}", response_model=ProviderOut)
async def update_provider(
    provider_id: uuid.UUID,
    body: ProviderIn,
    tenant_id: TenantId = Depends(require_tenant),
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
    box: SecretBox = Depends(get_secret_box),
    public_url: str | None = Depends(get_public_url),
) -> ProviderOut:
    spec = _spec_or_422(body.channel, body.name)
    async with uow:
        existing = await uow.providers.get(tenant_id, ProviderId(provider_id))
        if existing is None:
            raise HTTPException(status_code=404, detail="Provider not found")
        if (existing.channel, existing.name) != (body.channel, body.name):
            raise HTTPException(
                status_code=422,
                detail="A saved provider's type cannot change; create a new one",
            )
        try:
            stored = box.decrypt(existing.secrets_encrypted)
        except SecretsDecryptionError:
            raise HTTPException(status_code=409, detail="Stored secrets cannot be decrypted")
        except SecretsNotConfigured as exc:
            raise HTTPException(status_code=503, detail=str(exc))
        merged = {**stored}
        for key, value in body.secrets.items():
            if value in (None, ""):
                merged.pop(key, None)
            else:
                merged[key] = value
        try:
            config, secrets = spec.validate(
                body.config, merged, existing_secret_keys=frozenset()
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        updated = Provider(
            id=existing.id,
            tenant_id=tenant_id,
            channel=body.channel,
            name=body.name,
            label=body.label or existing.label or spec.name,
            config=config,
            secrets_encrypted=_encrypt_or_503(box, secrets),
            priority=body.priority,
            is_active=body.is_active,
        )
        await uow.providers.update(updated)
        await uow.commit()
        return _out(await uow.providers.get(tenant_id, existing.id), box, public_url=public_url)


@router.delete("/{provider_id}", status_code=204)
async def delete_provider(
    provider_id: uuid.UUID,
    tenant_id: TenantId = Depends(require_tenant),
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
) -> None:
    async with uow:
        in_use = _workflow_usage(await uow.workflows.list(tenant_id)).get(str(provider_id))
        if in_use:
            names = ", ".join(sorted({u["name"] for u in in_use}))
            raise HTTPException(
                status_code=409,
                detail=f"Provider is used by workflow(s): {names}. Remove it from their chains first.",
            )
        deleted = await uow.providers.delete(tenant_id, ProviderId(provider_id))
        await uow.commit()
    if not deleted:
        raise HTTPException(status_code=404, detail="Provider not found")
