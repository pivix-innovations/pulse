"""E2: multi-step workflows -- in-app, wait, then email only if still unread."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


from config.reconciliation import ReconciliationSettings
from domain.entities.preference import Preference
from domain.entities.workflow import Workflow
from domain.enums import ChannelType, DeliveryStatus, NotificationStatus
from domain.entities.step_run import StepRunStatus
from domain.value_objects.ids import PreferenceId, TenantId, WorkflowId
from domain.value_objects.quiet_hours import QuietHours
from services.notification_service import IncomingEvent, NotificationService
from services.ports.clients import ResolvedRecipient
from services.reconciliation_service import ReconciliationService
from services.rendering_service import RenderingService
from services.sequence_service import SequenceEngine, StepRunner
from tests.fakes import InMemoryUnitOfWork, StaticSentinelClient
from workflows.registry import WorkflowRegistry

STEPS = [
    {"id": "nudge", "channel": "in_app", "subject": "Hi {{ recipient.external_ref }}", "body": "Open the app", "format": "text"},
    {"id": "mail", "channel": "email", "subject": "You missed this", "body": "Still waiting", "format": "text",
     "delay": "10m", "when": {"status": "unread"}},
]


class Rig:
    def __init__(self, steps=STEPS, recipients=None):
        self.uow = InMemoryUnitOfWork()
        self.tenant = TenantId.new()
        workflow = Workflow(
            id=WorkflowId.new(), tenant_id=self.tenant, name="nudge", event_type="order.placed",
            definition={"steps": steps},
        )
        self.uow.workflows.items[str(workflow.id)] = workflow
        self.service = NotificationService(
            uow_factory=lambda: self.uow,
            sentinel=StaticSentinelClient(recipients or [ResolvedRecipient(external_ref="u1", email="u1@example.com")]),
            registry=WorkflowRegistry(), rendering=RenderingService(),
        )
        settings = ReconciliationSettings()
        self.runner = StepRunner(lambda: self.uow, SequenceEngine(RenderingService()), settings)
        self.sweep = ReconciliationService(lambda: self.uow, RenderingService(), settings)

    async def send(self, payload=None):
        self.nid = await self.service.process_event(
            self.tenant, IncomingEvent(event_type="order.placed", source="t", payload=payload or {})
        )
        return self.nid

    @property
    def notification(self):
        return self.uow.notifications.items[str(self.nid)]

    def attempts(self):
        return {a.channel: a for a in self.uow.delivery_attempts.items.values()}

    def runs(self):
        return {r.step_id: r for r in self.uow.step_runs.items.values()}

    def time_passes(self):
        """Make every waiting step due now."""
        for r in self.uow.step_runs.items.values():
            if r.status is StepRunStatus.SCHEDULED:
                r.run_at = datetime.now(timezone.utc) - timedelta(seconds=1)


async def test_only_the_first_step_is_sent_at_ingest_and_the_rest_wait():
    rig = Rig()
    await rig.send()
    assert list(rig.attempts()) == [ChannelType.IN_APP]
    assert rig.attempts()[ChannelType.IN_APP].subject == "Hi u1"
    runs = rig.runs()
    assert runs["nudge"].status is StepRunStatus.DONE
    assert runs["mail"].status is StepRunStatus.SCHEDULED
    assert runs["mail"].run_at - datetime.now(timezone.utc) > timedelta(minutes=9)
    # first step's job is queued like any delivery, the waiting step's is not
    assert [e.topic for e in rig.uow.outbox.items.values()] == ["deliver.in_app"]


async def test_nothing_runs_before_the_delay_passes():
    rig = Rig()
    await rig.send()
    assert await rig.runner.run_due_once() == 0
    assert list(rig.attempts()) == [ChannelType.IN_APP]


async def test_an_unread_message_triggers_the_email():
    rig = Rig()
    await rig.send()
    rig.time_passes()
    assert await rig.runner.run_due_once() == 1
    assert set(rig.attempts()) == {ChannelType.IN_APP, ChannelType.EMAIL}
    email = rig.attempts()[ChannelType.EMAIL]
    assert (email.subject, email.status) == ("You missed this", DeliveryStatus.PENDING)
    assert [e.topic for e in rig.uow.outbox.items.values()] == ["deliver.in_app", "deliver.email"]
    assert rig.runs()["mail"].status is StepRunStatus.DONE and rig.runs()["mail"].attempt_id == email.id


async def test_a_message_read_in_time_skips_the_email_and_settles_the_notification():
    rig = Rig()
    await rig.send()
    in_app = rig.attempts()[ChannelType.IN_APP]
    in_app.mark_sent(datetime.now(timezone.utc))
    in_app.mark_delivered(datetime.now(timezone.utc))     # the recipient read it

    rig.time_passes()
    await rig.runner.run_due_once()

    assert list(rig.attempts()) == [ChannelType.IN_APP]
    mail = rig.runs()["mail"]
    assert mail.status is StepRunStatus.SKIPPED and "delivered" in mail.reason
    assert rig.notification.status is NotificationStatus.COMPLETED


async def test_the_notification_stays_open_while_a_step_is_still_to_run():
    rig = Rig()
    await rig.send()
    in_app = rig.attempts()[ChannelType.IN_APP]
    in_app.mark_sent(datetime.now(timezone.utc))
    in_app.mark_delivered(datetime.now(timezone.utc))
    # every attempt is terminal, but the email step has not run yet
    assert rig.notification.status is NotificationStatus.PROCESSING
    assert await rig.sweep.sweep_once() == 0
    assert rig.notification.status is NotificationStatus.PROCESSING


async def test_the_sweep_does_not_give_up_on_an_unread_message_while_the_sequence_runs():
    rig = Rig()
    await rig.send()
    in_app = rig.attempts()[ChannelType.IN_APP]
    in_app.mark_sent(datetime.now(timezone.utc) - timedelta(hours=1))    # unconfirmed far past the in-app TTL
    await rig.sweep.sweep_once()
    assert in_app.status is DeliveryStatus.SENT and rig.uow.dead_letters.items == []


async def test_a_failed_first_step_can_trigger_a_different_channel():
    steps = [
        {"id": "push", "channel": "push", "body": "b", "format": "text"},
        {"id": "sms", "channel": "sms", "body": "b", "format": "text", "when": {"status": "failed"}},
    ]
    rig = Rig(steps)
    await rig.send()
    push = rig.attempts()[ChannelType.PUSH]
    push.mark_failed("no device")
    push.mark_dead("no device")
    rig.time_passes()
    await rig.runner.run_due_once()
    assert ChannelType.SMS in rig.attempts()


async def test_a_skipped_step_skips_everything_that_depends_on_it():
    steps = STEPS + [
        {"id": "text", "channel": "sms", "body": "last chance", "format": "text", "delay": "1h",
         "when": {"status": "not_delivered"}},    # of the previous step: the email
    ]
    rig = Rig(steps)
    await rig.send()
    in_app = rig.attempts()[ChannelType.IN_APP]
    in_app.mark_sent(datetime.now(timezone.utc))
    in_app.mark_delivered(datetime.now(timezone.utc))
    rig.time_passes()
    await rig.runner.run_due_once()      # email skipped
    rig.time_passes()
    await rig.runner.run_due_once()      # sms depends on a skipped step -> skipped
    assert list(rig.attempts()) == [ChannelType.IN_APP]
    assert rig.runs()["text"].status is StepRunStatus.SKIPPED
    assert rig.notification.status is NotificationStatus.COMPLETED


async def test_a_later_step_can_test_an_earlier_one_by_id():
    steps = STEPS + [
        {"id": "text", "channel": "sms", "body": "b", "format": "text", "delay": "1h",
         "when": {"step": "nudge", "status": "not_delivered"}},
    ]
    rig = Rig(steps)
    await rig.send()
    rig.time_passes()
    await rig.runner.run_due_once()       # email goes out
    rig.time_passes()
    await rig.runner.run_due_once()       # nudge is still unread -> sms goes out too
    assert set(rig.attempts()) == {ChannelType.IN_APP, ChannelType.EMAIL, ChannelType.SMS}


async def test_a_payload_condition_gates_a_step():
    steps = [
        {"id": "mail", "channel": "email", "body": "b", "format": "text"},
        {"id": "vip", "channel": "sms", "body": "b", "format": "text",
         "when": {"payload": [{"field": "plan", "op": "eq", "value": "pro"}]}},
    ]
    free, pro = Rig(steps), Rig(steps)
    await free.send({"plan": "free"})
    await pro.send({"plan": "pro"})
    for rig in (free, pro):
        rig.time_passes()
        await rig.runner.run_due_once()
    assert ChannelType.SMS not in free.attempts() and "plan" in free.runs()["vip"].reason
    assert ChannelType.SMS in pro.attempts()


async def test_an_opt_out_skips_the_step():
    rig = Rig()
    await rig.send()
    recipient = next(iter(rig.uow.recipients.items.values()))
    await rig.uow.preferences.upsert(Preference(
        id=PreferenceId.new(), tenant_id=rig.tenant, recipient_id=recipient.id,
        channel=ChannelType.EMAIL, opted_in=False,
    ))
    rig.time_passes()
    await rig.runner.run_due_once()
    assert ChannelType.EMAIL not in rig.attempts()
    assert "opted out" in rig.runs()["mail"].reason


async def test_quiet_hours_hold_a_step_until_the_window_ends():
    rig = Rig()
    await rig.send()
    recipient = next(iter(rig.uow.recipients.items.values()))
    now = datetime.now(timezone.utc)
    await rig.uow.preferences.upsert(Preference(
        id=PreferenceId.new(), tenant_id=rig.tenant, recipient_id=recipient.id, channel=ChannelType.EMAIL,
        quiet_hours=QuietHours(start=(now - timedelta(hours=1)).time(), end=(now + timedelta(hours=1)).time()),
    ))
    rig.time_passes()
    await rig.runner.run_due_once()
    email = rig.attempts()[ChannelType.EMAIL]
    assert email.next_retry_at is not None and email.next_retry_at > now
    entry = next(e for e in rig.uow.outbox.items.values() if e.aggregate_id == str(email.id))
    assert "defer_until" in entry.payload


async def test_every_recipient_gets_their_own_progress():
    rig = Rig(recipients=[
        ResolvedRecipient(external_ref="reader", email="r@example.com"),
        ResolvedRecipient(external_ref="ghost", email="g@example.com"),
    ])
    await rig.send()
    by_ref = {rig.uow.recipients.items[k].external_ref: k for k in rig.uow.recipients.items}
    reader_attempt = next(a for a in rig.uow.delivery_attempts.items.values() if str(a.recipient_id) == by_ref["reader"])
    reader_attempt.mark_sent(datetime.now(timezone.utc))
    reader_attempt.mark_delivered(datetime.now(timezone.utc))
    rig.time_passes()
    await rig.runner.run_due_once()
    emails = [a for a in rig.uow.delivery_attempts.items.values() if a.channel is ChannelType.EMAIL]
    assert [str(a.recipient_id) for a in emails] == [by_ref["ghost"]]     # only the one who did not read it


async def test_a_delayed_first_step_waits_for_the_scheduler():
    steps = [{"id": "later", "channel": "email", "body": "b", "format": "text", "delay": "5m"}]
    rig = Rig(steps)
    await rig.send()
    assert rig.attempts() == {} and rig.notification.status is NotificationStatus.PROCESSING
    rig.time_passes()
    await rig.runner.run_due_once()
    assert ChannelType.EMAIL in rig.attempts()


async def test_running_a_step_twice_does_not_duplicate_the_send():
    rig = Rig()
    await rig.send()
    rig.time_passes()
    await rig.runner.run_due_once()
    await rig.runner.run_due_once()
    assert len([a for a in rig.uow.delivery_attempts.items.values() if a.channel is ChannelType.EMAIL]) == 1


async def test_editing_the_workflow_mid_flight_skips_a_step_that_no_longer_exists():
    rig = Rig()
    await rig.send()
    workflow = next(iter(rig.uow.workflows.items.values()))
    workflow.definition = {"steps": [STEPS[0]]}          # the email step was removed
    rig.time_passes()
    await rig.runner.run_due_once()
    assert rig.runs()["mail"].status is StepRunStatus.SKIPPED and "no longer" in rig.runs()["mail"].reason


async def test_classic_workflows_are_untouched():
    rig = Rig(steps=[])
    workflow = next(iter(rig.uow.workflows.items.values()))
    workflow.definition = {"channels": [
        {"channel": "email", "subject": "s", "body": "b", "format": "text"},
        {"channel": "sms", "subject": None, "body": "b", "format": "text"},
    ]}
    await rig.send()
    assert set(rig.attempts()) == {ChannelType.EMAIL, ChannelType.SMS} and rig.uow.step_runs.items == {}
