"""
Saved templates (E4): draft/publish/rollback, and resolving a workflow's
template reference to the published version when a message is built.

A template row's own subject/body/format are its DRAFT. Publishing freezes a
copy as the next numbered version and points `published_version` at it;
rolling back publishes a copy of an older version as a NEW version, so the
history only ever moves forward.
"""
from __future__ import annotations

from typing import Any

from domain.entities.template import Template, TemplateVersion
from domain.enums import ChannelType
from domain.value_objects.ids import TemplateId, TemplateVersionId, TenantId
from services.ports.uow import UnitOfWork
from workflows.channel_template import ChannelTemplate
from workflows.registry import WorkflowDefinition

DEFAULT_LOCALE = "en"


class NothingToPublish(Exception):
    """The draft is identical to what is already published."""


class UnknownVersion(LookupError):
    pass


class TemplateUnavailable(LookupError):
    """A workflow references a template that cannot be rendered (missing, inactive or never published)."""


async def publish(
    uow: UnitOfWork, template: Template, note: str | None = None, *, force: bool = False
) -> TemplateVersion:
    """Freeze the draft as a new version and make it the published one. Caller commits."""
    if not force and template.published_version is not None:
        current = await uow.templates.get_version(template.tenant_id, template.id, template.published_version)
        if current is not None and current.same_content(template):
            raise NothingToPublish("The draft matches the published version")
    stored = await uow.templates.add_version(
        TemplateVersion(
            id=TemplateVersionId.new(), tenant_id=template.tenant_id, template_id=template.id, version=0,
            subject=template.subject, body=template.body, format=template.format, note=note,
        )
    )
    await uow.templates.set_published(template.tenant_id, template.id, stored.version)
    template.published_version = stored.version
    return stored


async def rollback(
    uow: UnitOfWork, template: Template, version: int, note: str | None = None
) -> TemplateVersion:
    """Publish a copy of an older version (and make it the draft too, so the editor shows what is live)."""
    old = await uow.templates.get_version(template.tenant_id, template.id, version)
    if old is None:
        raise UnknownVersion(f"Version {version} does not exist")
    template.subject, template.body, template.format = old.subject, old.body, old.format
    await uow.templates.update(template)
    return await publish(uow, template, note or f"Rolled back to version {version}", force=True)


async def resolve(
    uow: UnitOfWork, tenant_id: TenantId, template: ChannelTemplate, locale: str | None = None
) -> ChannelTemplate:
    """Inline templates pass through; a reference becomes the published content of the best-matching locale."""
    if not template.template:
        return template
    candidates = [
        t for t in await uow.templates.find(tenant_id, template.template, template.channel)
        if t.is_active and t.published_version is not None
    ]
    if not candidates:
        raise TemplateUnavailable(
            f"Template '{template.template}' ({template.channel.value}) is missing, inactive or has no published version"
        )
    wanted = [(locale or "").lower(), (locale or "").lower().split("-")[0], DEFAULT_LOCALE]
    chosen = next((t for loc in wanted for t in candidates if t.locale.lower() == loc), candidates[0])
    version = await uow.templates.get_version(tenant_id, chosen.id, chosen.published_version)
    if version is None:  # pragma: no cover - published_version always points at a stored version
        raise TemplateUnavailable(f"Template '{template.template}' lost its published version")
    return ChannelTemplate(
        channel=template.channel, subject=version.subject, body=version.body, format=version.format,
        template_id=str(chosen.id), template_version=version.version,
    )


def references(definition: dict[str, Any]) -> set[tuple[str, str]]:
    """Every (template name, channel) a workflow definition refers to."""
    parsed = WorkflowDefinition.from_dict("", "", definition)
    cts = [*parsed.channels, *parsed.fallback_templates, *(s.template for s in parsed.steps)]
    return {(ct.template, ct.channel.value) for ct in cts if ct.template}


async def workflows_using(uow: UnitOfWork, tenant_id: TenantId, name: str, channel: ChannelType) -> list[str]:
    """Names of the tenant's workflows that reference this template."""
    using = []
    for wf in await uow.workflows.list(tenant_id):
        try:
            if (name, channel.value) in references(wf.definition):
                using.append(wf.name)
        except (KeyError, ValueError):
            continue
    return using


def template_id_of(value) -> TemplateId:  # small helper for API code
    return value if isinstance(value, TemplateId) else TemplateId(value)
