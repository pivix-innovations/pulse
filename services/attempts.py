"""Building delivery attempts and their outbox entries (shared by ingest, steps and the sweep)."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Sequence

from domain.entities.delivery import DeliveryAttempt
from domain.entities.notification import Notification
from domain.entities.outbox import OutboxEntry
from domain.entities.recipient import Recipient
from domain.value_objects.ids import DeliveryAttemptId, OutboxEntryId
from services.ports.uow import UnitOfWork
from services.templates import resolve
from services.rendering_service import RenderingService
from workflows.channel_template import ChannelTemplate


def render_context(
    payload: dict[str, Any], recipient: dict[str, Any], extra: dict[str, Any] | None = None
) -> dict[str, Any]:
    """The variables a template sees: the event payload, `recipient`, and any extras (e.g. `digest`)."""
    return {**payload, "recipient": recipient, **(extra or {})}


def recipient_context(recipient: Recipient) -> dict[str, Any]:
    return {"external_ref": recipient.external_ref, "email": recipient.email, "locale": recipient.locale}


def build_attempt(
    rendering: RenderingService,
    notification: Notification,
    recipient: Recipient,
    template: ChannelTemplate,
    *,
    payload: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> DeliveryAttempt:
    """Render one channel. `payload` overrides the notification's (digests render the latest event);
    `extra` adds template variables (digests add `digest`)."""
    rendered = rendering.render(
        body=template.body,
        subject=template.subject,
        format=template.format,
        context=render_context(
            notification.payload if payload is None else payload, recipient_context(recipient), extra
        ),
    )
    return DeliveryAttempt(
        id=DeliveryAttemptId.new(),
        tenant_id=notification.tenant_id,
        notification_id=notification.id,
        recipient_id=recipient.id,
        channel=template.channel,
        subject=rendered.subject,
        body=rendered.text,
        body_html=rendered.html,
    )


def outbox_entries(notification: Notification, attempts: Sequence[DeliveryAttempt]) -> list[OutboxEntry]:
    """One `deliver.<channel>` entry per attempt; held attempts carry their `defer_until`."""
    return [
        OutboxEntry(
            id=OutboxEntryId.new(),
            tenant_id=notification.tenant_id,
            aggregate_type="delivery_attempt",
            aggregate_id=str(a.id),
            topic=f"deliver.{a.channel.value}",
            payload={
                "attempt_id": str(a.id),
                "tenant_id": str(notification.tenant_id),
                "channel": a.channel.value,
                **({"defer_until": a.next_retry_at.isoformat()} if a.next_retry_at is not None else {}),
            },
        )
        for a in attempts
    ]


async def attempts_for(
    uow: UnitOfWork,
    rendering: RenderingService,
    notification: Notification,
    recipient: Recipient,
    templates: Sequence[ChannelTemplate],
    now: datetime,
    *,
    payload: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> list[DeliveryAttempt]:
    """One attempt per channel the recipient has not opted out of; quiet hours hold an attempt until the window ends."""
    prefs = await uow.preferences.list_for_recipient(notification.tenant_id, recipient.id)
    opted_out = {p.channel for p in prefs if not p.opted_in}
    quiet = {p.channel: p.quiet_hours for p in prefs if p.quiet_hours is not None}
    attempts = []
    for template in templates:
        if template.channel in opted_out:
            continue
        template = await resolve(uow, notification.tenant_id, template, recipient.locale)
        attempt = build_attempt(rendering, notification, recipient, template, payload=payload, extra=extra)
        window = quiet.get(template.channel)
        if window is not None:
            attempt.next_retry_at = window.window_end(now)
        attempts.append(attempt)
    return attempts
