from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from domain.enums import ChannelType, TemplateFormat
from domain.value_objects.ids import TemplateId, TenantId


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
    created_at: datetime | None = None
    updated_at: datetime | None = None
