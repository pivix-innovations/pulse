from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from domain.enums import ChannelType, TemplateFormat
from workflows.channel_template import ChannelTemplate, decode_channel_template
from workflows.digest import DigestConfig, parse_digest
from workflows.steps import Step, encode_steps, parse_steps

__all__ = ["ChannelTemplate", "WorkflowDefinition", "WorkflowRegistry"]


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
    # When present the workflow is a sequence: steps replace `channels` and
    # `fallbacks` (each step decides what happens next).
    steps: tuple[Step, ...] = ()
    # When present, events are buffered per recipient and sent as one digest.
    digest: DigestConfig | None = None

    @property
    def is_sequence(self) -> bool:
        return bool(self.steps)

    def template_for(self, channel: ChannelType) -> ChannelTemplate | None:
        for ct in (*self.channels, *self.fallback_templates, *(s.template for s in self.steps)):
            if ct.channel is channel:
                return ct
        return None

    def step(self, step_id: str) -> Step | None:
        return next((s for s in self.steps if s.id == step_id), None)

    def as_dict(self) -> dict[str, Any]:
        """Serializable form persisted on the workflow DB row."""

        def encode(ct: ChannelTemplate) -> dict[str, Any]:
            if ct.template:
                return {"channel": ct.channel.value, "template": ct.template}
            return {
                "channel": ct.channel.value,
                "subject": ct.subject,
                "body": ct.body,
                "format": ct.format.value,
            }

        out: dict[str, Any] = {
            "channels": [encode(ct) for ct in self.channels],
            "fallbacks": self.fallbacks,
            "fallback_templates": [encode(ct) for ct in self.fallback_templates],
        }
        if self.steps:
            out["steps"] = encode_steps(self.steps)
        if self.digest:
            out["digest"] = self.digest.as_dict()
        return out

    @classmethod
    def from_dict(cls, name: str, event_type: str, raw: dict[str, Any]) -> "WorkflowDefinition":
        """Rebuild from a workflow DB row's definition JSON (sweep uses this)."""

        decode = decode_channel_template

        return cls(
            name=name,
            event_type=event_type,
            channels=tuple(decode(i) for i in raw.get("channels", [])),
            fallbacks=dict(raw.get("fallbacks", {})),
            fallback_templates=tuple(decode(i) for i in raw.get("fallback_templates", [])),
            steps=parse_steps(raw["steps"]) if raw.get("steps") else (),
            digest=parse_digest(raw["digest"]) if raw.get("digest") is not None else None,
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
