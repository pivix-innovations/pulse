from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from domain.enums import ChannelType, TemplateFormat
from domain.value_objects.ids import TemplateId, TemplateVersionId, TenantId


@dataclass(slots=True)
class Template:
    id: TemplateId
    tenant_id: TenantId
    name: str
    channel: ChannelType
    body: str
    subject: str | None = None
    format: TemplateFormat = TemplateFormat.MARKDOWN
    locale: str = "en"
    is_active: bool = True
    # The row's own subject/body/format are the DRAFT (working copy). Workflows
    # render the published version, a frozen snapshot in template_versions.
    published_version: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(slots=True)
class TemplateVersion:
    """An immutable snapshot of a template, created by publishing or rolling back."""

    id: TemplateVersionId
    tenant_id: TenantId
    template_id: TemplateId
    version: int
    body: str
    subject: str | None = None
    format: TemplateFormat = TemplateFormat.MARKDOWN
    note: str | None = None
    created_at: datetime | None = None

    def same_content(self, other: "Template | TemplateVersion") -> bool:
        return (self.subject, self.body, self.format) == (other.subject, other.body, other.format)
