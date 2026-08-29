from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from domain.enums import ChannelType, TemplateFormat


@dataclass(frozen=True, slots=True)
class ChannelTemplate:
    """Per-channel template carried inline on a workflow definition."""

    channel: ChannelType
    body: str
    subject: str | None = None
    format: TemplateFormat = TemplateFormat.MARKDOWN


@dataclass(frozen=True, slots=True)
class WorkflowDefinition:
    name: str
    event_type: str
    # Channels fanned out on ingress.
    channels: tuple[ChannelTemplate, ...]
    # Escalation map: source channel -> fallback channel, applied by the
    # reconciliation sweep when delivery goes unconfirmed. The chain is
    # capped by the map being finite and by one-attempt-per-channel.
    fallbacks: dict[str, str] = field(default_factory=dict)
    # Templates for channels reachable only via fallback.
    fallback_templates: tuple[ChannelTemplate, ...] = ()

    def template_for(self, channel: ChannelType) -> ChannelTemplate | None:
        for ct in (*self.channels, *self.fallback_templates):
            if ct.channel is channel:
                return ct
        return None

    def as_dict(self) -> dict[str, Any]:
        """Serializable form persisted on the workflow DB row."""

        def encode(ct: ChannelTemplate) -> dict[str, Any]:
            return {
                "channel": ct.channel.value,
                "subject": ct.subject,
                "body": ct.body,
                "format": ct.format.value,
            }

        return {
            "channels": [encode(ct) for ct in self.channels],
            "fallbacks": self.fallbacks,
            "fallback_templates": [encode(ct) for ct in self.fallback_templates],
        }

    @classmethod
    def from_dict(cls, name: str, event_type: str, raw: dict[str, Any]) -> "WorkflowDefinition":
        """Rebuild from a workflow DB row's definition JSON (sweep uses this)."""

        def decode(item: dict[str, Any]) -> ChannelTemplate:
            return ChannelTemplate(
                channel=ChannelType(item["channel"]),
                subject=item.get("subject"),
                body=item["body"],
                format=TemplateFormat(item.get("format", "markdown")),
            )

        return cls(
            name=name,
            event_type=event_type,
            channels=tuple(decode(i) for i in raw.get("channels", [])),
            fallbacks=dict(raw.get("fallbacks", {})),
            fallback_templates=tuple(decode(i) for i in raw.get("fallback_templates", [])),
        )


_WELCOME = WorkflowDefinition(
    name="welcome-user",
    event_type="user.signup",
    channels=(
        ChannelTemplate(
            channel=ChannelType.EMAIL,
            subject="Welcome to {{ app_name | default('Pulse') }}, {{ recipient.external_ref }}!",
            body=(
                "# Welcome!\n\n"
                "Hi **{{ recipient.external_ref }}**, thanks for signing up"
                "{% if plan %} for the *{{ plan }}* plan{% endif %}.\n\n"
                "We're glad you're here.\n"
            ),
            format=TemplateFormat.MARKDOWN,
        ),
        ChannelTemplate(
            channel=ChannelType.IN_APP,
            subject="Welcome, {{ recipient.external_ref }}!",
            body="Thanks for signing up{% if plan %} for the {{ plan }} plan{% endif %}.",
            format=TemplateFormat.TEXT,
        ),
        ChannelTemplate(
            channel=ChannelType.PUSH,
            subject="Welcome, {{ recipient.external_ref }}!",
            body="Thanks for signing up. Tap to get started.",
            format=TemplateFormat.TEXT,
        ),
    ),
    fallbacks={"in_app": "push", "push": "sms"},
    fallback_templates=(
        ChannelTemplate(
            channel=ChannelType.SMS,
            body=(
                "Welcome to {{ app_name | default('Pulse') }}, "
                "{{ recipient.external_ref }}! Thanks for signing up."
            ),
            format=TemplateFormat.TEXT,
        ),
    ),
)


class WorkflowRegistry:
    """
    Maps event types to workflow definitions. Phase 2 ships one hardcoded
    definition; DB-backed CRUD arrives in Phase 4.
    """

    def __init__(self, definitions: tuple[WorkflowDefinition, ...] = (_WELCOME,)) -> None:
        self._by_event_type = {d.event_type: d for d in definitions}

    def get(self, event_type: str) -> WorkflowDefinition | None:
        return self._by_event_type.get(event_type)
