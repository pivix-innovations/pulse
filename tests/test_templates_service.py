"""E4: saved templates -- draft/publish/rollback, and sending the published version."""
from __future__ import annotations

import pytest

from domain.entities.template import Template
from domain.entities.workflow import Workflow
from domain.enums import ChannelType, TemplateFormat
from domain.value_objects.ids import TemplateId, TenantId, WorkflowId
from services import templates as svc
from services.notification_service import IncomingEvent, NotificationService
from services.ports.clients import ResolvedRecipient
from services.rendering_service import RenderingService
from tests.fakes import InMemoryUnitOfWork, StaticSentinelClient
from workflows.channel_template import ChannelTemplate, decode_channel_template
from workflows.registry import WorkflowDefinition, WorkflowRegistry
from workflows.steps import InvalidSteps, parse_steps


def make(uow, tenant, name="welcome", body="Hello {{ recipient.external_ref }}", locale="en", channel=ChannelType.EMAIL, **kw):
    t = Template(id=TemplateId.new(), tenant_id=tenant, name=name, channel=channel, body=body,
                 subject=kw.pop("subject", "Hi"), locale=locale, format=TemplateFormat.TEXT, **kw)
    uow.templates.items[str(t.id)] = t
    return t


# ── publish / rollback ─────────────────────────────────────────────────


async def test_publishing_freezes_the_draft_as_numbered_versions():
    uow, tenant = InMemoryUnitOfWork(), TenantId.new()
    t = make(uow, tenant, body="v1 body")
    v1 = await svc.publish(uow, t, "first")
    t.body = "v2 body"
    v2 = await svc.publish(uow, t)
    t.body = "draft only"            # edits after publishing never touch published versions
    assert (v1.version, v2.version, t.published_version) == (1, 2, 2)
    assert (await uow.templates.get_version(tenant, t.id, 1)).body == "v1 body"
    assert (await uow.templates.get_version(tenant, t.id, 2)).body == "v2 body"


async def test_publishing_an_unchanged_draft_is_refused():
    uow, tenant = InMemoryUnitOfWork(), TenantId.new()
    t = make(uow, tenant)
    await svc.publish(uow, t)
    with pytest.raises(svc.NothingToPublish):
        await svc.publish(uow, t)
    t.subject = "Changed"
    assert (await svc.publish(uow, t)).version == 2


async def test_rollback_publishes_a_copy_so_history_only_moves_forward():
    uow, tenant = InMemoryUnitOfWork(), TenantId.new()
    t = make(uow, tenant, body="good")
    await svc.publish(uow, t)
    t.body = "bad typo"
    await svc.publish(uow, t)

    v3 = await svc.rollback(uow, t, 1)

    assert v3.version == 3 and v3.body == "good" and "version 1" in v3.note
    assert t.published_version == 3 and t.body == "good"     # the editor shows what is live
    assert [v.version for v in await uow.templates.list_versions(tenant, t.id)] == [3, 2, 1]
    with pytest.raises(svc.UnknownVersion):
        await svc.rollback(uow, t, 99)


# ── resolving references ───────────────────────────────────────────────


async def test_inline_templates_pass_through_untouched():
    uow = InMemoryUnitOfWork()
    inline = ChannelTemplate(channel=ChannelType.EMAIL, body="b", subject="s")
    assert await svc.resolve(uow, TenantId.new(), inline) is inline


async def test_a_reference_resolves_to_the_published_content_not_the_draft():
    uow, tenant = InMemoryUnitOfWork(), TenantId.new()
    t = make(uow, tenant, body="live")
    await svc.publish(uow, t)
    t.body = "work in progress"
    got = await svc.resolve(uow, tenant, ChannelTemplate(channel=ChannelType.EMAIL, template="welcome"))
    assert (got.body, got.subject, got.format) == ("live", "Hi", TemplateFormat.TEXT) and got.template is None


@pytest.mark.parametrize("problem", ["missing", "unpublished", "inactive", "wrong channel"])
async def test_an_unusable_reference_says_why(problem):
    uow, tenant = InMemoryUnitOfWork(), TenantId.new()
    if problem != "missing":
        t = make(uow, tenant, channel=ChannelType.SMS if problem == "wrong channel" else ChannelType.EMAIL)
        if problem != "unpublished":
            await svc.publish(uow, t)
        if problem == "inactive":
            t.is_active = False
    with pytest.raises(svc.TemplateUnavailable, match="welcome"):
        await svc.resolve(uow, tenant, ChannelTemplate(channel=ChannelType.EMAIL, template="welcome"))


async def test_another_tenants_template_is_invisible():
    uow, mine, theirs = InMemoryUnitOfWork(), TenantId.new(), TenantId.new()
    await svc.publish(uow, make(uow, theirs))
    with pytest.raises(svc.TemplateUnavailable):
        await svc.resolve(uow, mine, ChannelTemplate(channel=ChannelType.EMAIL, template="welcome"))


@pytest.mark.parametrize("recipient_locale, expected", [("fr", "Bonjour"), ("fr-CA", "Bonjour"), ("de", "Hello"), (None, "Hello")])
async def test_the_recipients_language_is_preferred_with_english_as_the_fallback(recipient_locale, expected):
    uow, tenant = InMemoryUnitOfWork(), TenantId.new()
    for loc, body in (("en", "Hello"), ("fr", "Bonjour")):
        await svc.publish(uow, make(uow, tenant, body=body, locale=loc))
    got = await svc.resolve(uow, tenant, ChannelTemplate(channel=ChannelType.EMAIL, template="welcome"), recipient_locale)
    assert got.body == expected


# ── definitions ────────────────────────────────────────────────────────


def test_references_parse_round_trip_and_reject_mixing_with_inline_text():
    d = WorkflowDefinition.from_dict("w", "e", {"channels": [{"channel": "email", "template": "welcome"}]})
    assert d.channels[0].template == "welcome" and d.as_dict()["channels"] == [{"channel": "email", "template": "welcome"}]
    assert svc.references(d.as_dict()) == {("welcome", "email")}
    with pytest.raises(ValueError, match="cannot also have its own body"):
        decode_channel_template({"channel": "email", "template": "welcome", "body": "x"})
    with pytest.raises(ValueError, match="name of a saved template"):
        decode_channel_template({"channel": "email", "template": ""})


def test_steps_can_reference_templates_too():
    steps = parse_steps([{"id": "a", "channel": "email", "template": "welcome"}])
    assert steps[0].template.template == "welcome"
    assert svc.references({"steps": [{"id": "a", "channel": "email", "template": "welcome"}]}) == {("welcome", "email")}
    with pytest.raises(InvalidSteps):
        parse_steps([{"id": "a", "channel": "email", "template": "welcome", "body": "x"}])


# ── in the pipeline ────────────────────────────────────────────────────


class Rig:
    def __init__(self, definition):
        self.uow, self.tenant = InMemoryUnitOfWork(), TenantId.new()
        wf = Workflow(id=WorkflowId.new(), tenant_id=self.tenant, name="w", event_type="e.v", definition=definition)
        self.uow.workflows.items[str(wf.id)] = wf
        self.service = NotificationService(
            uow_factory=lambda: self.uow,
            sentinel=StaticSentinelClient([ResolvedRecipient(external_ref="ada", email="ada@example.com", locale="fr")]),
            registry=WorkflowRegistry(), rendering=RenderingService(),
        )
        self.n = 0

    async def send(self):
        self.n += 1
        await self.service.process_event(self.tenant, IncomingEvent(event_type="e.v", source="t", idempotency_key=f"k{self.n}"))
        return [a for a in self.uow.delivery_attempts.items.values()][-1]


async def test_messages_are_built_from_the_published_version_and_pick_up_new_ones():
    rig = Rig({"channels": [{"channel": "email", "template": "welcome"}]})
    t = make(rig.uow, rig.tenant, body="v1 for {{ recipient.external_ref }}", subject="S1")
    await svc.publish(rig.uow, t)

    assert (await rig.send()).body == "v1 for ada"
    t.body = "draft v2"                                       # editing the draft changes nothing live
    assert (await rig.send()).body == "v1 for ada"
    await svc.publish(rig.uow, t)                              # publishing does
    assert (await rig.send()).body == "draft v2"
    await svc.rollback(rig.uow, t, 1)                          # and so does rolling back
    last = await rig.send()
    assert (last.body, last.subject) == ("v1 for ada", "S1")


async def test_the_recipients_locale_selects_the_translation():
    rig = Rig({"channels": [{"channel": "email", "template": "welcome"}]})
    await svc.publish(rig.uow, make(rig.uow, rig.tenant, body="Hello", locale="en"))
    await svc.publish(rig.uow, make(rig.uow, rig.tenant, body="Bonjour", locale="fr"))
    assert (await rig.send()).body == "Bonjour"                 # the recipient's locale is "fr"


async def test_ingest_fails_cleanly_when_a_referenced_template_cannot_be_used():
    rig = Rig({"channels": [{"channel": "email", "template": "ghost"}]})
    with pytest.raises(svc.TemplateUnavailable):
        await rig.send()
    assert rig.uow.delivery_attempts.items == {}


async def test_inline_and_saved_templates_mix_in_one_workflow():
    rig = Rig({"channels": [
        {"channel": "email", "template": "welcome"},
        {"channel": "sms", "subject": None, "body": "inline sms", "format": "text"},
    ]})
    await svc.publish(rig.uow, make(rig.uow, rig.tenant, body="saved email"))
    await rig.send()
    bodies = {a.channel: a.body for a in rig.uow.delivery_attempts.items.values()}
    assert bodies == {ChannelType.EMAIL: "saved email", ChannelType.SMS: "inline sms"}


async def test_an_unusable_fallback_template_dead_letters_instead_of_wedging_the_sweep():
    from datetime import datetime, timedelta, timezone

    from config.reconciliation import ReconciliationSettings
    from domain.entities.delivery import DeliveryAttempt
    from domain.entities.notification import Notification
    from domain.enums import DeliveryStatus, NotificationStatus
    from domain.value_objects.ids import DeliveryAttemptId, EventId, NotificationId, RecipientId
    from services.reconciliation_service import ReconciliationService

    rig = Rig({"channels": [{"channel": "in_app", "subject": "s", "body": "b", "format": "text"}],
               "fallbacks": {"in_app": "push"},
               "fallback_templates": [{"channel": "push", "template": "gone"}]})
    wf = next(iter(rig.uow.workflows.items.values()))
    from domain.entities.recipient import Recipient
    r = Recipient(id=RecipientId.new(), tenant_id=rig.tenant, external_ref="ada")
    rig.uow.recipients.items[str(r.id)] = r
    n = Notification(id=NotificationId.new(), tenant_id=rig.tenant, event_id=EventId.new(),
                     workflow_id=wf.id, status=NotificationStatus.PROCESSING)
    rig.uow.notifications.items[str(n.id)] = n
    a = DeliveryAttempt(id=DeliveryAttemptId.new(), tenant_id=rig.tenant, notification_id=n.id,
                        recipient_id=r.id, channel=ChannelType.IN_APP, subject="s", body="b")
    a.mark_sent(datetime.now(timezone.utc) - timedelta(hours=1))
    rig.uow.delivery_attempts.items[str(a.id)] = a

    sweep = ReconciliationService(lambda: rig.uow, RenderingService(), ReconciliationSettings())
    await sweep.sweep_once()                                  # must not raise

    assert a.status is DeliveryStatus.DEAD and len(rig.uow.dead_letters.items) == 1
