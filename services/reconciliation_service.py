from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from config.reconciliation import ReconciliationSettings
from domain.entities.dead_letter import DeadLetter
from domain.entities.delivery import DeliveryAttempt
from domain.entities.outbox import OutboxEntry
from domain.enums import ChannelType, DeliveryStatus
from domain.value_objects.ids import DeadLetterId, DeliveryAttemptId, OutboxEntryId
from services.ports.uow import UnitOfWork, UnitOfWorkFactory
from services.rendering_service import RenderingService
from services.settlement import settle_notification
from services.templates import TemplateUnavailable, resolve
from workflows.registry import WorkflowDefinition

logger = logging.getLogger(__name__)


class ReconciliationService:
    """
    The job that actually delivers the guarantee: finds attempts stuck in
    `sent` past a per-channel TTL with no confirmation (plus terminal
    `failed` ones, e.g. in-app with no live connection) and escalates them
    to the workflow's fallback channel, or dead-letters them.

    Escalation is capped structurally: the fallback map is finite and the
    (notification, recipient, channel) uniqueness means each channel is
    tried at most once per notification.
    """

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        rendering: RenderingService,
        settings: ReconciliationSettings,
    ) -> None:
        self._uow_factory = uow_factory
        self._rendering = rendering
        self._settings = settings

    def _ttls(self) -> dict[ChannelType, int]:
        return {
            ChannelType.IN_APP: self._settings.sse_ttl_seconds,
            ChannelType.PUSH: self._settings.push_ttl_seconds,
            ChannelType.SMS: self._settings.sms_ttl_seconds,
            ChannelType.EMAIL: self._settings.email_ttl_seconds,
        }

    async def sweep_once(self) -> int:
        """One pass over all channels; returns number of attempts acted on."""
        now = datetime.now(timezone.utc)
        handled = 0
        async with self._uow_factory() as uow:
            for channel, ttl in self._ttls().items():
                # Unconfirmed past TTL: provider accepted, receiver never confirmed.
                stale_sent = await uow.delivery_attempts.list_stale(
                    DeliveryStatus.SENT,
                    channel,
                    now - timedelta(seconds=ttl),
                    self._settings.batch_size,
                )
                # Terminal failures (non-retryable, e.g. no live SSE connection).
                stale_failed = await uow.delivery_attempts.list_stale(
                    DeliveryStatus.FAILED, channel, now, self._settings.batch_size
                )
                for attempt in stale_sent:
                    if await uow.step_runs.has_open(attempt.notification_id):
                        continue   # a sequence is still running: its next step may be the answer
                    await self._reconcile(uow, attempt)
                    handled += 1
                for attempt in stale_failed:
                    await self._reconcile(uow, attempt)
                    handled += 1
            handled += await self._requeue_stuck(uow, now)
            handled += await self._settle(uow)
            await uow.commit()
        if handled:
            logger.info("Reconciliation sweep handled %d attempt(s)", handled)
        return handled

    async def _requeue_stuck(self, uow: UnitOfWork, now: datetime) -> int:
        """Re-publish pending/retrying attempts whose queue job was lost or whose worker died."""
        stuck = await uow.delivery_attempts.list_stuck(
            lost_before=now - timedelta(seconds=self._settings.stuck_lost_ttl_seconds),
            in_doubt_before=now - timedelta(seconds=self._settings.stuck_in_doubt_ttl_seconds),
            limit=self._settings.batch_size,
        )
        for attempt in stuck:
            attempt.mark_requeued(now)
            await uow.delivery_attempts.update(attempt)
            await uow.outbox.add_many(
                [
                    OutboxEntry(
                        id=OutboxEntryId.new(),
                        tenant_id=attempt.tenant_id,
                        aggregate_type="delivery_attempt",
                        aggregate_id=str(attempt.id),
                        topic=f"deliver.{attempt.channel.value}",
                        payload={
                            "attempt_id": str(attempt.id),
                            "tenant_id": str(attempt.tenant_id),
                            "channel": attempt.channel.value,
                        },
                    )
                ]
            )
            logger.warning("Requeued stuck attempt %s (%s)", attempt.id, attempt.channel.value)
        return len(stuck)

    async def _settle(self, uow: UnitOfWork) -> int:
        """Move PROCESSING notifications whose attempts are all terminal to COMPLETED/FAILED."""
        settled = 0
        for notification in await uow.notifications.list_settleable(self._settings.batch_size):
            if await settle_notification(uow, notification.tenant_id, notification.id):
                settled += 1
        return settled

    async def _reconcile(self, uow: UnitOfWork, attempt: DeliveryAttempt) -> None:
        definition = await self._load_definition(uow, attempt)
        fallback_name = (definition.fallbacks if definition else {}).get(attempt.channel.value)
        reason = (
            f"Unconfirmed on '{attempt.channel.value}' past TTL"
            if attempt.status is DeliveryStatus.SENT
            else f"Failed on '{attempt.channel.value}': {attempt.last_error}"
        )

        if definition is not None and fallback_name is not None:
            escalated = await self._escalate(
                uow, attempt, definition, ChannelType(fallback_name), reason
            )
            if escalated:
                self._retire(attempt, f"{reason}; escalated to '{fallback_name}'")
                await uow.delivery_attempts.update(attempt)
                return
            reason = f"{reason}; fallback '{fallback_name}' already attempted"

        # No (usable) fallback: give up and record it.
        self._retire(attempt, reason)
        await uow.delivery_attempts.update(attempt)
        await uow.dead_letters.add(
            DeadLetter(
                id=DeadLetterId.new(),
                tenant_id=attempt.tenant_id,
                attempt_id=attempt.id,
                notification_id=attempt.notification_id,
                channel=attempt.channel,
                reason=reason,
            )
        )
        logger.warning("Attempt %s dead-lettered by sweep: %s", attempt.id, reason)

    def _retire(self, attempt: DeliveryAttempt, reason: str) -> None:
        attempt.mark_dead(reason)

    async def _escalate(
        self,
        uow: UnitOfWork,
        source: DeliveryAttempt,
        definition: WorkflowDefinition,
        fallback_channel: ChannelType,
        reason: str,
    ) -> bool:
        """Create + enqueue a fallback-channel attempt. False if capped."""
        existing = await uow.delivery_attempts.get_by_dispatch(
            source.notification_id, source.recipient_id, fallback_channel
        )
        if existing is not None:
            return False  # escalation cap: one attempt per channel

        template = definition.template_for(fallback_channel)
        recipient = await uow.recipients.get(source.tenant_id, source.recipient_id)
        notification = await uow.notifications.get(source.tenant_id, source.notification_id)
        if template is None or recipient is None or notification is None:
            logger.warning(
                "Cannot escalate attempt %s to '%s': missing template/recipient/notification",
                source.id,
                fallback_channel.value,
            )
            return False

        try:
            template = await resolve(uow, source.tenant_id, template, recipient.locale)
        except TemplateUnavailable as exc:
            # One bad template must not wedge the sweep: treat the fallback as unusable.
            logger.warning("Cannot escalate attempt %s to '%s': %s", source.id, fallback_channel.value, exc)
            return False
        rendered = self._rendering.render(
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
        fallback = DeliveryAttempt(
            id=DeliveryAttemptId.new(),
            tenant_id=source.tenant_id,
            notification_id=source.notification_id,
            recipient_id=source.recipient_id,
            channel=fallback_channel,
            subject=rendered.subject,
            body=rendered.text,
            body_html=rendered.html,
        )
        await uow.delivery_attempts.add_many([fallback])
        await uow.outbox.add_many(
            [
                OutboxEntry(
                    id=OutboxEntryId.new(),
                    tenant_id=source.tenant_id,
                    aggregate_type="delivery_attempt",
                    aggregate_id=str(fallback.id),
                    topic=f"deliver.{fallback_channel.value}",
                    payload={
                        "attempt_id": str(fallback.id),
                        "tenant_id": str(source.tenant_id),
                        "channel": fallback_channel.value,
                    },
                )
            ]
        )
        logger.info(
            "Escalated attempt %s (%s) to '%s' as %s: %s",
            source.id,
            source.channel.value,
            fallback_channel.value,
            fallback.id,
            reason,
        )
        return True

    async def _load_definition(
        self, uow: UnitOfWork, attempt: DeliveryAttempt
    ) -> WorkflowDefinition | None:
        notification = await uow.notifications.get(attempt.tenant_id, attempt.notification_id)
        if notification is None:
            return None
        workflow = await uow.workflows.get(attempt.tenant_id, notification.workflow_id)
        if workflow is None:
            return None
        return WorkflowDefinition.from_dict(workflow.name, workflow.event_type, workflow.definition)
