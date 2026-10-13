"""Building delivery attempts and their outbox entries (shared by ingest, steps and the sweep)."""
from __future__ import annotations

from typing import Sequence

from domain.entities.delivery import DeliveryAttempt
from domain.entities.notification import Notification
from domain.entities.outbox import OutboxEntry
from domain.entities.recipient import Recipient
from domain.value_objects.ids import DeliveryAttemptId, OutboxEntryId
from services.rendering_service import RenderingService
from workflows.channel_template import ChannelTemplate


def build_attempt(
    rendering: RenderingService,
    notification: Notification,
    recipient: Recipient,
    template: ChannelTemplate,
) -> DeliveryAttempt:
    rendered = rendering.render(
        body=template.body,
        subject=template.subject,
        format=template.format,
        context={
            **notification.payload,
            "recipient": {
                "external_ref": recipient.external_ref,
                "email": recipient.email,
                "locale": recipient.locale,
            },
        },
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
