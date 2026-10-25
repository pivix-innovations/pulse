"""
Multi-step workflows (E2). `SequenceEngine` runs one step for one recipient;
`StepRunner` is the scheduler loop that runs steps as they come due.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from config.reconciliation import ReconciliationSettings
from domain.entities.notification import Notification
from domain.entities.recipient import Recipient
from domain.entities.step_run import StepRun, StepRunStatus
from domain.enums import DeliveryStatus
from domain.value_objects.ids import StepRunId
from services.attempts import build_attempt, outbox_entries
from services.ports.uow import UnitOfWork, UnitOfWorkFactory
from services.rendering_service import RenderingService
from services.settlement import settle_notification
from services.send_policy import blocked_reason
from services.templates import resolve
from telemetry.metrics import get_metrics
from workflows.registry import WorkflowDefinition
from workflows.steps import Step, check_payload, status_matches

logger = logging.getLogger(__name__)


class SequenceEngine:
    def __init__(self, rendering: RenderingService) -> None:
        self._rendering = rendering

    async def start(
        self,
        uow: UnitOfWork,
        notification: Notification,
        definition: WorkflowDefinition,
        recipients: list[Recipient],
    ) -> int:
        """Begin the sequence for each recipient; step 1 runs now unless it has a delay. Returns attempts created."""
        now = datetime.now(timezone.utc)
        first = definition.steps[0]
        created = 0
        for recipient in recipients:
            run = StepRun(
                id=StepRunId.new(), tenant_id=notification.tenant_id, notification_id=notification.id,
                recipient_id=recipient.id, step_id=first.id, step_index=0,
                run_at=now + timedelta(seconds=first.delay_seconds),
            )
            if not await uow.step_runs.add(run):
                continue   # already started (scheduled notification dispatched twice)
            if first.delay_seconds == 0:
                created += await self.execute(uow, run, definition, notification, recipient, now)
        return created

    async def execute(
        self,
        uow: UnitOfWork,
        run: StepRun,
        definition: WorkflowDefinition,
        notification: Notification,
        recipient: Recipient | None,
        now: datetime,
    ) -> int:
        """Run one step: create its attempt or skip it, then schedule the next step. Returns attempts created."""
        step = definition.step(run.step_id)
        created = 0
        if step is None:
            self._skip(run, "step is no longer in the workflow", now)
        elif recipient is None:
            self._skip(run, "recipient no longer exists", now)
        else:
            ok, why = await self._should_run(uow, run, step, definition, notification)
            if not ok:
                self._skip(run, why, now)
            else:
                blocked, attempt = await self._create_attempt(uow, notification, recipient, step, now, definition.category)
                if attempt is None:
                    self._skip(run, blocked or f"{step.channel.value} already attempted", now)
                else:
                    run.mark_done(attempt.id, now)
                    created = 1
        await uow.step_runs.save(run)
        get_metrics().workflow_steps.add(
            1, {"channel": step.channel.value if step else "unknown", "outcome": run.status.value}
        )
        await self._schedule_next(uow, run, definition, notification, now)
        return created

    def _skip(self, run: StepRun, reason: str, now: datetime) -> None:
        run.mark_skipped(reason, now)
        logger.info("Step %s skipped for notification %s: %s", run.step_id, run.notification_id, reason)

    async def _schedule_next(
        self, uow: UnitOfWork, run: StepRun, definition: WorkflowDefinition, notification: Notification, now: datetime
    ) -> None:
        nxt_index = run.step_index + 1
        if nxt_index >= len(definition.steps):
            return
        nxt = definition.steps[nxt_index]
        await uow.step_runs.add(
            StepRun(
                id=StepRunId.new(), tenant_id=run.tenant_id, notification_id=run.notification_id,
                recipient_id=run.recipient_id, step_id=nxt.id, step_index=nxt_index,
                run_at=now + timedelta(seconds=nxt.delay_seconds),
            )
        )

    async def _create_attempt(self, uow, notification, recipient, step: Step, now, category: str | None = None):
        """(why it was blocked, the attempt): exactly one of them is set."""
        prefs = await uow.preferences.list_for_recipient(notification.tenant_id, recipient.id)
        blocked = await blocked_reason(uow, notification.tenant_id, recipient, step.channel, category, prefs=prefs)
        if blocked:
            return blocked, None
        if await uow.delivery_attempts.get_by_dispatch(notification.id, recipient.id, step.channel) is not None:
            return None, None
        template = await resolve(uow, notification.tenant_id, step.template, recipient.locale)
        attempt = build_attempt(self._rendering, notification, recipient, template, category=category)
        window = next((p.quiet_hours for p in prefs if p.channel is step.channel and p.quiet_hours), None)
        if window is not None:
            attempt.next_retry_at = window.window_end(now)   # held until their quiet window ends
        await uow.delivery_attempts.add_many([attempt])
        await uow.outbox.add_many(outbox_entries(notification, [attempt]))
        return None, attempt

    async def _should_run(self, uow, run, step: Step, definition, notification) -> tuple[bool, str]:
        cond = step.when
        if cond is None:
            return True, ""
        for check in cond.payload:
            if not check_payload(check, notification.payload):
                return False, f"payload condition on '{check.field}' not met"
        if cond.status is not None:
            ref_id = cond.step or definition.steps[run.step_index - 1].id
            state = await self._state_of(uow, notification, run, ref_id)
            if not status_matches(cond.status, state):
                return False, f"'{ref_id}' is {state}, condition needs {cond.status}"
        return True, ""

    async def _state_of(self, uow, notification, run, step_id: str) -> str:
        """How an earlier step ended up: delivered, pending, failed or skipped."""
        ref = await uow.step_runs.get(notification.id, run.recipient_id, step_id)
        if ref is None or ref.status is StepRunStatus.SKIPPED or ref.attempt_id is None:
            return "skipped"
        attempt = await uow.delivery_attempts.get(notification.tenant_id, ref.attempt_id)
        if attempt is None:
            return "failed"
        if attempt.reached_recipient:
            return "delivered"
        if attempt.status in (DeliveryStatus.DEAD, DeliveryStatus.FAILED):
            return "failed"
        return "pending"   # pending, retrying, or sent and awaiting confirmation


class StepRunner:
    """Scheduler loop: runs the steps whose time has come."""

    def __init__(
        self, uow_factory: UnitOfWorkFactory, engine: SequenceEngine, settings: ReconciliationSettings
    ) -> None:
        self._uow_factory = uow_factory
        self._engine = engine
        self._batch = settings.batch_size

    async def run_due_once(self) -> int:
        now = datetime.now(timezone.utc)
        ran = 0
        async with self._uow_factory() as uow:
            runs = await uow.step_runs.claim_due(now, self._batch)
            definitions: dict[str, WorkflowDefinition | None] = {}
            for run in runs:
                try:
                    notification = await uow.notifications.get(run.tenant_id, run.notification_id)
                    if notification is None:
                        self._cancel(run, "notification no longer exists", now)
                        await uow.step_runs.save(run)
                        continue
                    key = str(notification.workflow_id)
                    if key not in definitions:
                        wf = await uow.workflows.get(run.tenant_id, notification.workflow_id)
                        definitions[key] = (
                            WorkflowDefinition.from_dict(wf.name, wf.event_type, wf.definition) if wf else None
                        )
                    definition = definitions[key]
                    if definition is None:
                        self._cancel(run, "workflow no longer exists", now)
                        await uow.step_runs.save(run)
                        continue
                    recipient = await uow.recipients.get(run.tenant_id, run.recipient_id)
                    await self._engine.execute(uow, run, definition, notification, recipient, now)
                    await settle_notification(uow, run.tenant_id, run.notification_id)
                    ran += 1
                except Exception:
                    logger.exception("Step %s of notification %s failed", run.step_id, run.notification_id)
                    self._cancel(run, "internal error while running the step", now)
                    await uow.step_runs.save(run)
            await uow.commit()
        if ran:
            logger.info("Ran %d workflow step(s)", ran)
        return ran

    @staticmethod
    def _cancel(run: StepRun, reason: str, now: datetime) -> None:
        run.mark_skipped(reason, now)
