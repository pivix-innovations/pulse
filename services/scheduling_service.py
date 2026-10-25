from __future__ import annotations

import logging
from datetime import datetime, timezone

from domain.entities.notification import Notification
from services.notification_service import NotificationService
from services.ports.clients import SentinelClient, SentinelUnavailable
from services.ports.uow import UnitOfWork, UnitOfWorkFactory
from workflows.registry import WorkflowDefinition

logger = logging.getLogger(__name__)


class SchedulingService:
    """
    Dispatches scheduled notifications when they come due: re-resolves
    recipients, fans out attempts + outbox entries (one transaction), and
    moves the notification from CREATED to PROCESSING. Runs alongside the
    outbox relay in the scheduler worker.
    """

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        notifications: NotificationService,
        sentinel: SentinelClient,
        batch_size: int = 100,
    ) -> None:
        self._uow_factory = uow_factory
        self._notifications = notifications
        self._sentinel = sentinel
        self._batch_size = batch_size

    async def dispatch_due_once(self) -> int:
        """One pass; returns how many due notifications were fanned out."""
        now = datetime.now(timezone.utc)
        dispatched = 0
        async with self._uow_factory() as uow:
            for notification in await uow.notifications.list_due(now, self._batch_size):
                try:
                    await self._dispatch(uow, notification)
                    dispatched += 1
                except SentinelUnavailable as exc:
                    # Not the notification's fault: leave it CREATED and try on the next pass.
                    logger.warning(
                        "Scheduled notification %s waits: Sentinel unavailable (%s)", notification.id, exc
                    )
                except Exception:
                    logger.exception(
                        "Failed to dispatch scheduled notification %s", notification.id
                    )
                    notification.mark_failed()
                    await uow.notifications.update(notification)
            await uow.commit()
        if dispatched:
            logger.info("Dispatched %d scheduled notification(s)", dispatched)
        return dispatched

    async def _dispatch(self, uow: UnitOfWork, notification: Notification) -> None:
        workflow = await uow.workflows.get(notification.tenant_id, notification.workflow_id)
        if workflow is None:
            raise LookupError(f"Workflow {notification.workflow_id} missing")
        definition = WorkflowDefinition.from_dict(
            workflow.name, workflow.event_type, workflow.definition
        )
        # Recipients are re-resolved at fire time: preferences and rosters
        # may have changed between scheduling and dispatch.
        resolved = await self._sentinel.resolve_recipients(
            notification.tenant_id, workflow.event_type, notification.payload
        )
        if not resolved:
            raise LookupError("No recipients resolved at dispatch time")
        await self._notifications.fan_out(uow, notification, definition, resolved)
        notification.mark_processing()
        await uow.notifications.update(notification)
