from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Sequence

from domain.entities.delivery import DeliveryAttempt
from domain.entities.event import Event
from domain.entities.notification import Notification
from domain.entities.recipient import Recipient
from domain.entities.workflow import Workflow
from domain.value_objects.ids import (
    EventId,
    NotificationId,
    RecipientId,
    TenantId,
    WorkflowId,
)
from services.ports.clients import ResolvedRecipient, SentinelClient
from services.ports.uow import UnitOfWork, UnitOfWorkFactory
from services.attempts import build_attempt, outbox_entries
from services.rendering_service import RenderingService
from services.sequence_service import SequenceEngine
from workflows.registry import WorkflowDefinition, WorkflowRegistry


class UnknownEventType(Exception):
    def __init__(self, event_type: str) -> None:
        super().__init__(f"No workflow registered for event type '{event_type}'")
        self.event_type = event_type


class NoRecipientsResolved(Exception):
    def __init__(self, event_type: str) -> None:
        super().__init__(f"No recipients resolved for event type '{event_type}'")
        self.event_type = event_type


@dataclass(slots=True)
class IncomingEvent:
    event_type: str
    source: str
    payload: dict[str, Any] = field(default_factory=dict)
    idempotency_key: str | None = None
    occurred_at: datetime | None = None
    # Future instant -> fan-out deferred until then (scheduling service).
    scheduled_at: datetime | None = None


class NotificationService:
    """
    Ingress pipeline: resolve workflow + recipients + preferences, render
    per channel, then persist notification + delivery attempts + outbox
    entries in ONE transaction (transactional outbox). Scheduled events
    persist the notification only; the scheduler fans out when due.
    """

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        sentinel: SentinelClient,
        registry: WorkflowRegistry,
        rendering: RenderingService,
    ) -> None:
        self._uow_factory = uow_factory
        self._sentinel = sentinel
        self._registry = registry
        self._rendering = rendering
        self._sequences = SequenceEngine(rendering)

    async def process_event(self, tenant_id: TenantId, incoming: IncomingEvent) -> NotificationId:
        # Idempotent ingress: same key -> same notification, no new fan-out.
        # Checked first so a re-sent event is answered without calling Sentinel
        # (which may be down) and without paying for another lookup.
        if incoming.idempotency_key:
            async with self._uow_factory() as uow:
                existing_event = await uow.events.get_by_idempotency_key(
                    tenant_id, incoming.idempotency_key
                )
                if existing_event is not None:
                    existing = await uow.notifications.get_by_event(tenant_id, existing_event.id)
                    if existing is not None:
                        return existing.id

        resolved = await self._sentinel.resolve_recipients(
            tenant_id, incoming.event_type, incoming.payload
        )
        if not resolved:
            raise NoRecipientsResolved(incoming.event_type)

        async with self._uow_factory() as uow:
            workflow, definition = await self._resolve_workflow(
                uow, tenant_id, incoming.event_type
            )

            event = Event(
                id=EventId.new(),
                tenant_id=tenant_id,
                event_type=incoming.event_type,
                source=incoming.source,
                payload=incoming.payload,
                idempotency_key=incoming.idempotency_key,
                occurred_at=incoming.occurred_at,
                received_at=datetime.now(timezone.utc),
            )
            await uow.events.add(event)

            notification = Notification(
                id=NotificationId.new(),
                tenant_id=tenant_id,
                event_id=event.id,
                workflow_id=workflow.id,
                payload=incoming.payload,
                scheduled_at=incoming.scheduled_at,
            )
            await uow.notifications.add(notification)

            now = datetime.now(timezone.utc)
            if incoming.scheduled_at is not None and incoming.scheduled_at > now:
                # Deferred: stays CREATED until the scheduler dispatches it.
                await uow.commit()
                return notification.id

            await self.fan_out(uow, notification, definition, resolved)
            notification.mark_processing()
            await uow.notifications.update(notification)
            await uow.commit()
            return notification.id

    async def _resolve_workflow(
        self, uow: UnitOfWork, tenant_id: TenantId, event_type: str
    ) -> tuple[Workflow, WorkflowDefinition]:
        """A tenant's DB-managed workflow wins; the code registry is the default."""
        stored = await uow.workflows.get_active_by_event_type(tenant_id, event_type)
        if stored is not None:
            return stored, WorkflowDefinition.from_dict(
                stored.name, stored.event_type, stored.definition
            )

        definition = self._registry.get(event_type)
        if definition is None:
            raise UnknownEventType(event_type)
        workflow = await uow.workflows.get_or_create(
            Workflow(
                id=WorkflowId.new(),
                tenant_id=tenant_id,
                name=definition.name,
                event_type=definition.event_type,
                definition=definition.as_dict(),
            )
        )
        return workflow, definition

    async def fan_out(
        self,
        uow: UnitOfWork,
        notification: Notification,
        definition: WorkflowDefinition,
        resolved: Sequence[ResolvedRecipient],
    ) -> int:
        """
        Render + persist one DeliveryAttempt and one outbox entry per
        (recipient x opted-in channel), inside the caller's transaction.
        Shared by ingress and the scheduler. Returns the attempt count.
        """
        attempts: list[DeliveryAttempt] = []
        recipients: list[Recipient] = []
        for r in resolved:
            recipient = await uow.recipients.upsert_by_external_ref(
                Recipient(
                    id=RecipientId.new(),
                    tenant_id=notification.tenant_id,
                    external_ref=r.external_ref,
                    email=r.email,
                    phone=r.phone,
                    locale=r.locale,
                    timezone=r.timezone,
                )
            )
            recipients.append(recipient)
            if definition.is_sequence:
                continue
            prefs = await uow.preferences.list_for_recipient(notification.tenant_id, recipient.id)
            opted_out = {p.channel for p in prefs if not p.opted_in}
            quiet = {p.channel: p.quiet_hours for p in prefs if p.quiet_hours is not None}
            now = datetime.now(timezone.utc)
            for ct in definition.channels:
                if ct.channel in opted_out:
                    continue
                attempt = self._build_attempt(notification, recipient, ct)
                window = quiet.get(ct.channel)
                if window is not None:
                    # Hold until the recipient's quiet window ends.
                    attempt.next_retry_at = window.window_end(now)
                attempts.append(attempt)

        if definition.is_sequence:
            # Steps decide what is sent and when; the first one runs now (unless delayed).
            return await self._sequences.start(uow, notification, definition, recipients)

        await uow.delivery_attempts.add_many(attempts)
        await uow.outbox.add_many(outbox_entries(notification, attempts))
        return len(attempts)

    def _build_attempt(
        self,
        notification: Notification,
        recipient: Recipient,
        channel_template,
    ) -> DeliveryAttempt:
        return build_attempt(self._rendering, notification, recipient, channel_template)
