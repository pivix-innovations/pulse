from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from api.dependencies import get_secret_box, get_uow, require_tenant
from domain.entities.provider import Provider
from domain.enums import ChannelType
from domain.value_objects.ids import ProviderId, TenantId
from providers.catalog import ProviderSpec, get_spec, list_specs
from repositories.uow import SqlAlchemyUnitOfWork
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
    used_by: list[dict[str, Any]] = Field(default_factory=list)
    created_at: datetime | None
    updated_at: datetime | None


def _stored_secrets(box: SecretBox, p: Provider) -> dict[str, Any]:
    try:
        return box.decrypt(p.secrets_encrypted)
    except (SecretsNotConfigured, SecretsDecryptionError):
        return {}


def _out(
    p: Provider, box: SecretBox, used_by: list[dict[str, Any]] | None = None
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
        return _out(await uow.providers.get(tenant_id, provider.id), box)


@router.get("", response_model=list[ProviderOut])
async def list_providers(
    channel: ChannelType | None = None,
    tenant_id: TenantId = Depends(require_tenant),
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
    box: SecretBox = Depends(get_secret_box),
) -> list[ProviderOut]:
    async with uow:
        providers = await uow.providers.list(tenant_id)
        usage = _workflow_usage(await uow.workflows.list(tenant_id))
    return [
        _out(p, box, usage.get(str(p.id)))
        for p in providers
        if channel is None or p.channel is channel
    ]


@router.get("/{provider_id}", response_model=ProviderOut)
async def get_provider(
    provider_id: uuid.UUID,
    tenant_id: TenantId = Depends(require_tenant),
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
    box: SecretBox = Depends(get_secret_box),
) -> ProviderOut:
    async with uow:
        provider = await uow.providers.get(tenant_id, ProviderId(provider_id))
        usage = _workflow_usage(await uow.workflows.list(tenant_id))
    if provider is None:
        raise HTTPException(status_code=404, detail="Provider not found")
    return _out(provider, box, usage.get(str(provider.id)))


@router.put("/{provider_id}", response_model=ProviderOut)
async def update_provider(
    provider_id: uuid.UUID,
    body: ProviderIn,
    tenant_id: TenantId = Depends(require_tenant),
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
    box: SecretBox = Depends(get_secret_box),
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
        return _out(await uow.providers.get(tenant_id, existing.id), box)


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
