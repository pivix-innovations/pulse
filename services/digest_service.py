"""Digest flushing (E3): the scheduler turns each closed batch into one consolidated message."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from types import SimpleNamespace

from config.reconciliation import ReconciliationSettings
from domain.entities.digest import DigestBatch
from services.attempts import attempts_for, outbox_entries
from services.ports.uow import UnitOfWork, UnitOfWorkFactory
from services.rendering_service import RenderingService
from services.settlement import settle_notification
from telemetry.metrics import get_metrics
from workflows.registry import WorkflowDefinition

logger = logging.getLogger(__name__)


class DigestFlusher:
    def __init__(
        self, uow_factory: UnitOfWorkFactory, rendering: RenderingService, settings: ReconciliationSettings
    ) -> None:
        self._uow_factory = uow_factory
        self._rendering = rendering
        self._batch = settings.batch_size

    async def flush_due_once(self) -> int:
        """Send every batch whose window has closed (or that is full). Returns digests sent."""
        now = datetime.now(timezone.utc)
        sent = 0
        async with self._uow_factory() as uow:
            for batch in await uow.digests.claim_due(now, self._batch):
                try:
                    await self._flush(uow, batch, now)
                    sent += 1
                    get_metrics().digest_flushes.add(1, {"outcome": "sent"})
                except Exception:
                    # A batch that cannot be rendered would otherwise be retried forever.
                    logger.exception("Digest %s failed; dropping it", batch.id)
                    await self._fail(uow, batch, now)
                    get_metrics().digest_flushes.add(1, {"outcome": "failed"})
            await uow.commit()
        if sent:
            logger.info("Sent %d digest(s)", sent)
        return sent

    async def _flush(self, uow: UnitOfWork, batch: DigestBatch, now: datetime) -> None:
        items = await uow.digests.list_items(batch.id)
        anchor = await uow.notifications.get(batch.tenant_id, batch.anchor_notification_id)
        workflow = await uow.workflows.get(batch.tenant_id, batch.workflow_id)
        recipient = await uow.recipients.get(batch.tenant_id, batch.recipient_id)
        if anchor is None or workflow is None or recipient is None or not items:
            raise LookupError("digest is missing its notification, workflow or recipient")
        definition = WorkflowDefinition.from_dict(workflow.name, workflow.event_type, workflow.definition)

        payloads = [i.payload for i in items]
        # An object, not a dict: in Jinja `digest.items` on a dict is the dict's own .items() method.
        digest = SimpleNamespace(count=len(payloads), items=payloads, first=payloads[0], last=payloads[-1])
        attempts = await attempts_for(
            uow, self._rendering, anchor, recipient, definition.channels, now,
            payload=payloads[-1], extra={"digest": digest},
        )
        await uow.delivery_attempts.add_many(attempts)
        await uow.outbox.add_many(outbox_entries(anchor, attempts))
        await uow.digests.mark_flushed(batch, now)
        await self._settle_all(uow, batch, items)

    async def _fail(self, uow: UnitOfWork, batch: DigestBatch, now: datetime) -> None:
        items = await uow.digests.list_items(batch.id)
        await uow.digests.mark_flushed(batch, now)
        for nid in {i.notification_id for i in items} | {batch.anchor_notification_id}:
            notification = await uow.notifications.get(batch.tenant_id, nid)
            # Another recipient's batch may still be waiting on this event.
            if notification is not None and not await uow.digests.has_open_for_notification(nid):
                notification.mark_failed()
                await uow.notifications.update(notification)

    async def _settle_all(self, uow: UnitOfWork, batch: DigestBatch, items) -> None:
        """Every event in the batch is now accounted for: the anchor by its attempts, the rest by being merged in."""
        for nid in {i.notification_id for i in items} | {batch.anchor_notification_id}:
            await settle_notification(uow, batch.tenant_id, nid)
