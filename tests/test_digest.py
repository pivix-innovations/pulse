"""E3: digest / batching -- many events, one message."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from config.reconciliation import ReconciliationSettings
from domain.entities.digest import DigestStatus
from domain.entities.preference import Preference
from domain.entities.workflow import Workflow
from domain.enums import ChannelType, NotificationStatus
from domain.value_objects.ids import PreferenceId, TenantId, WorkflowId
from services.digest_service import DigestFlusher
from services.notification_service import IncomingEvent, NotificationService
from services.ports.clients import ResolvedRecipient
from services.rendering_service import RenderingService
from tests.fakes import InMemoryUnitOfWork, StaticSentinelClient
from workflows.digest import DigestConfig, InvalidDigest, parse_digest
from workflows.registry import WorkflowDefinition, WorkflowRegistry

SUBJECT = "{% if digest.count > 1 %}{{ digest.count }} new comments{% else %}New comment{% endif %}"
BODY = "{% for c in digest.items %}- {{ c.text }}\n{% endfor %}latest: {{ text }} on {{ post }}"


# ── config ─────────────────────────────────────────────────────────────


def test_digest_config_parses_and_round_trips():
    cfg = parse_digest({"window": "10m", "max_items": 25, "key": "post.id"})
    assert cfg == DigestConfig(window_seconds=600, max_items=25, key="post.id")
    d = WorkflowDefinition.from_dict("w", "e", {"channels": [{"channel": "email", "body": "b"}], "digest": cfg.as_dict()})
    assert d.digest == cfg and d.as_dict()["digest"] == cfg.as_dict()


@pytest.mark.parametrize("raw, message", [
    ("10m", "object"),
    ({}, "window"),
    ({"window": "soon"}, "window"),
    ({"window": 0}, "between 1 second and 7 days"),
    ({"window": "8d"}, "between 1 second and 7 days"),
    ({"window": "1m", "max_items": 1}, "max_items"),
    ({"window": "1m", "max_items": 5000}, "max_items"),
    ({"window": "1m", "max_items": True}, "max_items"),
    ({"window": "1m", "key": ""}, "key"),
    ({"window": "1m", "colour": "red"}, "unknown keys"),
])
def test_bad_digest_configs_are_rejected(raw, message):
    with pytest.raises(InvalidDigest, match=message):
        parse_digest(raw)


def test_the_key_groups_by_a_payload_field():
    cfg = DigestConfig(60, key="post.id")
    assert cfg.key_for({"post": {"id": 7}}) == "7"
    assert cfg.key_for({"post": {}}) == "" and cfg.key_for({}) == ""
    assert DigestConfig(60).key_for({"post": {"id": 7}}) == ""


# ── behaviour ──────────────────────────────────────────────────────────


class Rig:
    def __init__(self, digest=None, recipients=None, channels=None):
        self.uow = InMemoryUnitOfWork()
        self.tenant = TenantId.new()
        workflow = Workflow(
            id=WorkflowId.new(), tenant_id=self.tenant, name="comments", event_type="comment.added",
            definition={
                "channels": channels or [{"channel": "email", "subject": SUBJECT, "body": BODY, "format": "text"}],
                "digest": digest or {"window": "10m", "max_items": 5},
            },
        )
        self.uow.workflows.items[str(workflow.id)] = workflow
        self.service = NotificationService(
            uow_factory=lambda: self.uow,
            sentinel=StaticSentinelClient(recipients or [ResolvedRecipient(external_ref="u1", email="u1@example.com")]),
            registry=WorkflowRegistry(), rendering=RenderingService(),
        )
        self.flusher = DigestFlusher(lambda: self.uow, RenderingService(), ReconciliationSettings())
        self.n = 0

    async def comment(self, text, post="p1"):
        self.n += 1
        return await self.service.process_event(
            self.tenant, IncomingEvent(event_type="comment.added", source="t", payload={"text": text, "post": post},
                                       idempotency_key=f"k{self.n}"))

    def due(self):
        for b in self.uow.digests.batches.values():
            if b.status is DigestStatus.OPEN:
                b.flush_at = datetime.now(timezone.utc) - timedelta(seconds=1)

    def attempts(self):
        return list(self.uow.delivery_attempts.items.values())

    def status(self, nid):
        return self.uow.notifications.items[str(nid)].status


async def test_events_are_buffered_not_sent():
    rig = Rig()
    n1, n2 = await rig.comment("first"), await rig.comment("second")
    assert rig.attempts() == [] and rig.uow.outbox.items == {}
    [batch] = rig.uow.digests.batches.values()
    assert batch.item_count == 2 and batch.anchor_notification_id == n1
    assert batch.flush_at - datetime.now(timezone.utc) > timedelta(minutes=9)
    assert rig.status(n1) is rig.status(n2) is NotificationStatus.PROCESSING   # not settled while buffered


async def test_nothing_is_sent_before_the_window_closes():
    rig = Rig()
    await rig.comment("first")
    assert await rig.flusher.flush_due_once() == 0 and rig.attempts() == []


async def test_a_closed_window_sends_one_consolidated_message():
    rig = Rig()
    n1, n2, n3 = await rig.comment("first"), await rig.comment("second"), await rig.comment("third")
    rig.due()
    assert await rig.flusher.flush_due_once() == 1

    [attempt] = rig.attempts()
    assert attempt.notification_id == n1 and attempt.channel is ChannelType.EMAIL
    assert attempt.subject == "3 new comments"
    assert attempt.body == "- first\n- second\n- third\nlatest: third on p1"      # items in order, latest payload
    assert [e.topic for e in rig.uow.outbox.items.values()] == ["deliver.email"]
    assert next(iter(rig.uow.digests.batches.values())).status is DigestStatus.FLUSHED
    # the merged-in events are accounted for; the anchor waits for its delivery like any other
    assert rig.status(n2) is rig.status(n3) is NotificationStatus.COMPLETED
    assert rig.status(n1) is NotificationStatus.PROCESSING


async def test_a_single_event_still_renders_with_the_same_template():
    rig = Rig()
    await rig.comment("only")
    rig.due()
    await rig.flusher.flush_due_once()
    [attempt] = rig.attempts()
    assert attempt.subject == "New comment" and attempt.body.startswith("- only")


async def test_reaching_max_items_makes_the_digest_due_at_once():
    rig = Rig(digest={"window": "1h", "max_items": 3})
    for i in range(3):
        await rig.comment(f"c{i}")
    assert await rig.flusher.flush_due_once() == 1       # no waiting for the hour
    assert rig.attempts()[0].subject == "3 new comments"


async def test_a_new_event_after_a_flush_starts_a_fresh_digest():
    rig = Rig()
    await rig.comment("a")
    rig.due()
    await rig.flusher.flush_due_once()
    n = await rig.comment("b")
    open_batches = [b for b in rig.uow.digests.batches.values() if b.status is DigestStatus.OPEN]
    assert len(open_batches) == 1 and open_batches[0].anchor_notification_id == n


async def test_each_recipient_has_their_own_digest():
    rig = Rig(recipients=[ResolvedRecipient(external_ref="a", email="a@e.com"), ResolvedRecipient(external_ref="b", email="b@e.com")])
    await rig.comment("hello")
    await rig.comment("again")
    rig.due()
    await rig.flusher.flush_due_once()
    assert len(rig.attempts()) == 2 and {a.subject for a in rig.attempts()} == {"2 new comments"}
    assert len({a.recipient_id for a in rig.attempts()}) == 2


async def test_the_key_keeps_unrelated_events_in_separate_digests():
    rig = Rig(digest={"window": "10m", "key": "post"})
    await rig.comment("a1", post="A")
    await rig.comment("b1", post="B")
    await rig.comment("a2", post="A")
    rig.due()
    await rig.flusher.flush_due_once()
    subjects = sorted(a.subject for a in rig.attempts())
    assert subjects == ["2 new comments", "New comment"]


async def test_opt_outs_are_honoured_when_the_digest_is_sent():
    rig = Rig(channels=[
        {"channel": "email", "subject": SUBJECT, "body": BODY, "format": "text"},
        {"channel": "sms", "body": "{{ digest.count }} new", "format": "text"},
    ])
    await rig.comment("hi")
    recipient = next(iter(rig.uow.recipients.items.values()))
    await rig.uow.preferences.upsert(Preference(
        id=PreferenceId.new(), tenant_id=rig.tenant, recipient_id=recipient.id,
        channel=ChannelType.EMAIL, opted_in=False))
    rig.due()
    await rig.flusher.flush_due_once()
    assert [a.channel for a in rig.attempts()] == [ChannelType.SMS]


async def test_a_digest_for_a_fully_opted_out_recipient_just_completes():
    rig = Rig()
    n = await rig.comment("hi")
    recipient = next(iter(rig.uow.recipients.items.values()))
    await rig.uow.preferences.upsert(Preference(
        id=PreferenceId.new(), tenant_id=rig.tenant, recipient_id=recipient.id,
        channel=ChannelType.EMAIL, opted_in=False))
    rig.due()
    await rig.flusher.flush_due_once()
    assert rig.attempts() == [] and rig.status(n) is NotificationStatus.COMPLETED


async def test_a_broken_template_drops_the_digest_instead_of_retrying_forever():
    rig = Rig(channels=[{"channel": "email", "subject": "{{ ", "body": "b", "format": "text"}])
    n1, n2 = await rig.comment("a"), await rig.comment("b")
    rig.due()
    assert await rig.flusher.flush_due_once() == 0
    assert rig.status(n1) is rig.status(n2) is NotificationStatus.FAILED
    assert await rig.flusher.flush_due_once() == 0           # nothing left to retry


async def test_resending_an_event_does_not_add_it_twice():
    rig = Rig()
    first = await rig.service.process_event(
        rig.tenant, IncomingEvent(event_type="comment.added", source="t", payload={"text": "x"}, idempotency_key="same"))
    again = await rig.service.process_event(
        rig.tenant, IncomingEvent(event_type="comment.added", source="t", payload={"text": "x"}, idempotency_key="same"))
    assert first == again and next(iter(rig.uow.digests.batches.values())).item_count == 1


async def test_classic_workflows_never_touch_digests():
    rig = Rig()
    workflow = next(iter(rig.uow.workflows.items.values()))
    workflow.definition = {"channels": [{"channel": "email", "subject": "s", "body": "b", "format": "text"}]}
    await rig.comment("x")
    assert len(rig.attempts()) == 1 and rig.uow.digests.batches == {}
