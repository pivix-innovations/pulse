from __future__ import annotations

import difflib
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from api.dependencies import get_uow, require_scope
from domain.entities.template import Template, TemplateVersion
from domain.enums import ChannelType, TemplateFormat
from domain.value_objects.ids import TemplateId, TenantId
from renderers.jinja_renderer import TemplateRenderError
from repositories.uow import SqlAlchemyUnitOfWork
from services import templates as svc
from services.attempts import render_context

router = APIRouter(prefix="/templates", tags=["templates"])

SAMPLE_RECIPIENT = {"external_ref": "sample-user", "email": "sample@example.com", "locale": "en"}


class TemplateIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    channel: ChannelType
    body: str = Field(min_length=1)
    subject: str | None = Field(default=None, max_length=500)
    format: TemplateFormat = TemplateFormat.MARKDOWN
    locale: str = Field(default="en", max_length=16)
    is_active: bool = True


class TemplateCreateIn(TemplateIn):
    publish: bool = Field(default=False, description="Publish the first version straight away")


class TemplateOut(TemplateIn):
    """subject/body/format are the DRAFT; workflows send the published version."""

    id: uuid.UUID
    published_version: int | None = None
    has_unpublished_changes: bool = False
    created_at: datetime | None
    updated_at: datetime | None


class VersionOut(BaseModel):
    version: int
    subject: str | None
    body: str
    format: TemplateFormat
    note: str | None
    is_published: bool
    created_at: datetime | None


class DiffSide(BaseModel):
    label: str          # "v3" or "draft"
    subject: str | None
    body: str


class DiffOut(BaseModel):
    """Line diffs (unified format, no file headers) between two versions, or a version and the draft."""

    from_: DiffSide = Field(serialization_alias="from")
    to: DiffSide
    subject_diff: list[str]
    body_diff: list[str]
    changed: bool


class VersionUsageOut(BaseModel):
    version: int
    messages: int
    last_used_at: datetime | None
    is_published: bool


class PublishIn(BaseModel):
    note: str | None = Field(default=None, max_length=500)


class RollbackIn(PublishIn):
    version: int = Field(ge=1)


class PreviewIn(BaseModel):
    data: dict[str, Any] = Field(default_factory=dict, description="Sample event payload")
    recipient: dict[str, Any] | None = Field(default=None, description="Sample recipient (external_ref, email, locale)")
    version: str = Field(default="draft", description="'draft', 'published' or a version number")
    digest: list[dict[str, Any]] | None = Field(
        default=None, description="Sample digest: the payloads of the events being batched"
    )


class AdHocPreviewIn(PreviewIn):
    channel: ChannelType = ChannelType.EMAIL
    subject: str | None = None
    body: str = Field(min_length=1)
    format: TemplateFormat = TemplateFormat.MARKDOWN


class PreviewOut(BaseModel):
    subject: str | None
    text: str
    html: str | None
    version: str          # what was rendered: "draft" or a version number


def _out(t: Template, published: TemplateVersion | None = None) -> TemplateOut:
    return TemplateOut(
        id=t.id.value, name=t.name, channel=t.channel, body=t.body, subject=t.subject, format=t.format,
        locale=t.locale, is_active=t.is_active, published_version=t.published_version,
        has_unpublished_changes=published is None or not published.same_content(t),
        created_at=t.created_at, updated_at=t.updated_at,
    )


def _version_out(v: TemplateVersion, published: int | None) -> VersionOut:
    return VersionOut(
        version=v.version, subject=v.subject, body=v.body, format=v.format, note=v.note,
        is_published=v.version == published, created_at=v.created_at,
    )


async def _load(uow: SqlAlchemyUnitOfWork, tenant_id: TenantId, template_id: uuid.UUID) -> Template:
    template = await uow.templates.get(tenant_id, TemplateId(template_id))
    if template is None:
        raise HTTPException(status_code=404, detail="Template not found")
    return template


async def _out_for(uow: SqlAlchemyUnitOfWork, template: Template) -> TemplateOut:
    published = (
        await uow.templates.get_version(template.tenant_id, template.id, template.published_version)
        if template.published_version is not None else None
    )
    return _out(template, published)


@router.post("", response_model=TemplateOut, status_code=201)
async def create_template(
    body: TemplateCreateIn,
    tenant_id: TenantId = Depends(require_scope("admin")),
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
) -> TemplateOut:
    template = Template(id=TemplateId.new(), tenant_id=tenant_id, **body.model_dump(exclude={"publish"}))
    async with uow:
        await uow.templates.add(template)
        if body.publish:
            await svc.publish(uow, template, "First version")
        await uow.commit()
        return await _out_for(uow, await uow.templates.get(tenant_id, template.id))


@router.get("", response_model=list[TemplateOut])
async def list_templates(
    tenant_id: TenantId = Depends(require_scope("read")),
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
) -> list[TemplateOut]:
    async with uow:
        published = await uow.templates.list_published(tenant_id)
        return [_out(t, published.get(t.id)) for t in await uow.templates.list(tenant_id)]


@router.post("/preview", response_model=PreviewOut)
async def preview_unsaved(
    body: AdHocPreviewIn,
    request: Request,
    tenant_id: TenantId = Depends(require_scope("admin")),
) -> PreviewOut:
    """Render text that is not saved anywhere yet (an editor's current contents, or a workflow's inline message)."""
    return _render(request, body.body, body.subject, body.format, body, "draft")


@router.get("/{template_id}", response_model=TemplateOut)
async def get_template(
    template_id: uuid.UUID,
    tenant_id: TenantId = Depends(require_scope("read")),
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
) -> TemplateOut:
    async with uow:
        return await _out_for(uow, await _load(uow, tenant_id, template_id))


@router.put("/{template_id}", response_model=TemplateOut)
async def update_template(
    template_id: uuid.UUID,
    body: TemplateIn,
    tenant_id: TenantId = Depends(require_scope("admin")),
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
) -> TemplateOut:
    """Edits the DRAFT. What workflows send does not change until the draft is published."""
    async with uow:
        existing = await _load(uow, tenant_id, template_id)
        identity_changes = (existing.name, existing.channel) != (body.name, body.channel)
        if identity_changes or (existing.is_active and not body.is_active):
            using = await svc.workflows_using(uow, tenant_id, existing.name, existing.channel)
            if using:
                what = "renamed or moved to another channel" if identity_changes else "deactivated"
                raise HTTPException(
                    status_code=409,
                    detail=f"Template is used by workflow(s) {', '.join(using)} and cannot be {what}",
                )
        updated = Template(
            id=existing.id, tenant_id=tenant_id, published_version=existing.published_version,
            **body.model_dump(),
        )
        await uow.templates.update(updated)
        await uow.commit()
        return await _out_for(uow, await uow.templates.get(tenant_id, existing.id))


@router.delete("/{template_id}", status_code=204)
async def delete_template(
    template_id: uuid.UUID,
    tenant_id: TenantId = Depends(require_scope("admin")),
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
) -> None:
    async with uow:
        existing = await _load(uow, tenant_id, template_id)
        using = await svc.workflows_using(uow, tenant_id, existing.name, existing.channel)
        if using:
            raise HTTPException(
                status_code=409, detail=f"Template is used by workflow(s) {', '.join(using)} and cannot be deleted"
            )
        await uow.templates.delete(tenant_id, existing.id)
        await uow.commit()


# ── versions ───────────────────────────────────────────────────────────


@router.get("/{template_id}/versions", response_model=list[VersionOut])
async def list_versions(
    template_id: uuid.UUID,
    tenant_id: TenantId = Depends(require_scope("read")),
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
) -> list[VersionOut]:
    """History, newest first."""
    async with uow:
        template = await _load(uow, tenant_id, template_id)
        versions = await uow.templates.list_versions(tenant_id, template.id)
    return [_version_out(v, template.published_version) for v in versions]


@router.get("/{template_id}/versions/{version}", response_model=VersionOut)
async def get_version(
    template_id: uuid.UUID,
    version: int,
    tenant_id: TenantId = Depends(require_scope("read")),
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
) -> VersionOut:
    async with uow:
        template = await _load(uow, tenant_id, template_id)
        found = await uow.templates.get_version(tenant_id, template.id, version)
    if found is None:
        raise HTTPException(status_code=404, detail="Version not found")
    return _version_out(found, template.published_version)


def _unified(a: str | None, b: str | None) -> list[str]:
    lines = list(difflib.unified_diff((a or "").splitlines(), (b or "").splitlines(), lineterm="", n=2))
    return [ln for ln in lines if not ln.startswith(("---", "+++"))]


@router.get("/{template_id}/diff", response_model=DiffOut, response_model_by_alias=True)
async def diff_versions(
    template_id: uuid.UUID,
    from_: str = Query(alias="from", description="A version number"),
    to: str = Query(default="draft", description="A version number, or 'draft'"),
    tenant_id: TenantId = Depends(require_scope("read")),
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
) -> DiffOut:
    """What changed between two versions (or a version and the current draft)."""
    async with uow:
        template = await _load(uow, tenant_id, template_id)

        async def side(ref: str) -> DiffSide:
            if ref == "draft":
                return DiffSide(label="draft", subject=template.subject, body=template.body)
            if not ref.isdigit():
                raise HTTPException(status_code=422, detail="Use a version number or 'draft'")
            found = await uow.templates.get_version(tenant_id, template.id, int(ref))
            if found is None:
                raise HTTPException(status_code=404, detail=f"Version {ref} not found")
            return DiffSide(label=f"v{found.version}", subject=found.subject, body=found.body)

        a, b = await side(from_), await side(to)
    subject_diff, body_diff = _unified(a.subject, b.subject), _unified(a.body, b.body)
    return DiffOut(from_=a, to=b, subject_diff=subject_diff, body_diff=body_diff, changed=bool(subject_diff or body_diff))


@router.get("/{template_id}/usage", response_model=list[VersionUsageOut])
async def version_usage(
    template_id: uuid.UUID,
    tenant_id: TenantId = Depends(require_scope("read")),
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
) -> list[VersionUsageOut]:
    """How many messages each version has been used to render, newest version first."""
    async with uow:
        template = await _load(uow, tenant_id, template_id)
        rows = await uow.delivery_attempts.template_usage(tenant_id, template.id)
    return [
        VersionUsageOut(version=v, messages=n, last_used_at=last, is_published=v == template.published_version)
        for v, n, last in rows
    ]


@router.post("/{template_id}/publish", response_model=TemplateOut)
async def publish_template(
    template_id: uuid.UUID,
    body: PublishIn | None = None,
    tenant_id: TenantId = Depends(require_scope("admin")),
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
) -> TemplateOut:
    """Freeze the draft as a new version and send that from now on."""
    async with uow:
        template = await _load(uow, tenant_id, template_id)
        try:
            await svc.publish(uow, template, body.note if body else None)
        except svc.NothingToPublish as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        await uow.commit()
        return await _out_for(uow, await uow.templates.get(tenant_id, template.id))


@router.post("/{template_id}/rollback", response_model=TemplateOut)
async def rollback_template(
    template_id: uuid.UUID,
    body: RollbackIn,
    tenant_id: TenantId = Depends(require_scope("admin")),
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
) -> TemplateOut:
    """Go back to an earlier version by publishing a copy of it as a new version."""
    async with uow:
        template = await _load(uow, tenant_id, template_id)
        try:
            await svc.rollback(uow, template, body.version, body.note)
        except svc.UnknownVersion:
            raise HTTPException(status_code=404, detail="Version not found")
        await uow.commit()
        return await _out_for(uow, await uow.templates.get(tenant_id, template.id))


# ── preview ────────────────────────────────────────────────────────────


def _render(request: Request, body: str, subject: str | None, fmt: TemplateFormat, p: PreviewIn, label: str) -> PreviewOut:
    recipient = {**SAMPLE_RECIPIENT, **(p.recipient or {})}
    extra = None
    if p.digest:
        from types import SimpleNamespace

        extra = {"digest": SimpleNamespace(count=len(p.digest), items=p.digest, first=p.digest[0], last=p.digest[-1])}
    payload = p.digest[-1] if p.digest and not p.data else p.data
    try:
        rendered = request.app.state.rendering.render(
            body=body, subject=subject, format=fmt, context=render_context(payload, recipient, extra)
        )
    except TemplateRenderError as exc:
        raise HTTPException(status_code=422, detail=f"Template does not render: {exc}")
    return PreviewOut(subject=rendered.subject, text=rendered.text, html=rendered.html, version=label)


@router.post("/{template_id}/preview", response_model=PreviewOut)
async def preview_template(
    template_id: uuid.UUID,
    body: PreviewIn,
    request: Request,
    tenant_id: TenantId = Depends(require_scope("read")),
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
) -> PreviewOut:
    """
    Render a template with sample data, exactly as a message would be built.
    `version` is "draft" (default), "published" or a version number.
    """
    async with uow:
        template = await _load(uow, tenant_id, template_id)
        if body.version == "draft":
            source, label = template, "draft"
        else:
            number = template.published_version if body.version == "published" else None
            if body.version != "published":
                try:
                    number = int(body.version)
                except ValueError:
                    raise HTTPException(status_code=422, detail="version must be 'draft', 'published' or a number")
            if number is None:
                raise HTTPException(status_code=409, detail="This template has no published version yet")
            source = await uow.templates.get_version(tenant_id, template.id, number)
            if source is None:
                raise HTTPException(status_code=404, detail="Version not found")
            label = str(number)
    return _render(request, source.body, source.subject, source.format, body, label)
