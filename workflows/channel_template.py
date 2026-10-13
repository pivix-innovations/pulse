from __future__ import annotations

from dataclasses import dataclass

from domain.enums import ChannelType, TemplateFormat


@dataclass(frozen=True, slots=True)
class ChannelTemplate:
    """
    Per-channel message on a workflow definition: written inline (`body`,
    `subject`, `format`) or a reference to a saved template by name
    (`template`), whose published version is looked up when a message is
    built (see services.templates.resolve).
    """

    channel: ChannelType
    body: str = ""
    subject: str | None = None
    format: TemplateFormat = TemplateFormat.MARKDOWN
    template: str | None = None


def decode_channel_template(item: dict) -> ChannelTemplate:
    """Parse one channel entry: inline (`body`) or a saved-template reference (`template`), not both."""
    channel = ChannelType(item["channel"])
    ref = item.get("template")
    if ref is not None:
        if not isinstance(ref, str) or not ref.strip():
            raise ValueError("'template' must be the name of a saved template")
        if item.get("body") or item.get("subject"):
            raise ValueError(f"channel '{channel.value}' uses a saved template, so it cannot also have its own body or subject")
        return ChannelTemplate(channel=channel, template=ref.strip())
    return ChannelTemplate(
        channel=channel,
        subject=item.get("subject"),
        body=item["body"],
        format=TemplateFormat(item.get("format", "markdown")),
    )
