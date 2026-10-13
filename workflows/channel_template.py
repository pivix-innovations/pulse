from __future__ import annotations

from dataclasses import dataclass

from domain.enums import ChannelType, TemplateFormat


@dataclass(frozen=True, slots=True)
class ChannelTemplate:
    """Per-channel template carried inline on a workflow definition."""

    channel: ChannelType
    body: str
    subject: str | None = None
    format: TemplateFormat = TemplateFormat.MARKDOWN
