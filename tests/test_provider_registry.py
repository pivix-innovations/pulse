from __future__ import annotations

from config.loader import get_settings
from domain.entities.provider import Provider
from domain.entities.workflow import Workflow
from domain.enums import ChannelType
from domain.value_objects.ids import ProviderId, TenantId, WorkflowId
from providers.registry import ProviderRegistry
from services.secrets import SecretBox, generate_key
from tests.fakes import InMemoryUnitOfWork

TENANT = TenantId.new()


def registry(box=None):
    return ProviderRegistry(get_settings(), redis=None, secret_box=box or SecretBox(generate_key()))


async def add_provider(uow, name="console", channel=ChannelType.EMAIL, label=None, **kw):
    p = Provider(
        id=ProviderId.new(), tenant_id=TENANT, channel=channel, name=name,
        label=label or f"{name}-{len(uow.providers.items)}", **kw,
    )
    await uow.providers.add(p)
    return p


async def add_workflow(uow, chains):
    w = Workflow(
        id=WorkflowId.new(), tenant_id=TENANT, name="wf", event_type="x",
        definition={"provider_chains": chains},
    )
    uow.workflows.items[str(w.id)] = w
    return w


async def labels(reg, uow, workflow=None, channel=ChannelType.EMAIL):
    chain = await reg.resolve_chain(uow, TENANT, channel, workflow.id if workflow else None)
    return [r.label for r in chain]


async def test_workflow_chain_is_returned_in_its_priority_order():
    uow = InMemoryUnitOfWork()
    a = await add_provider(uow, label="A")
    b = await add_provider(uow, label="B")
    c = await add_provider(uow, label="C")
    wf = await add_workflow(uow, {"email": [str(c.id), str(a.id), str(b.id)]})
    assert await labels(registry(), uow, wf) == ["C", "A", "B"]


async def test_same_saved_provider_can_serve_several_workflows():
    uow = InMemoryUnitOfWork()
    a = await add_provider(uow, label="A")
    b = await add_provider(uow, label="B")
    w1 = await add_workflow(uow, {"email": [str(a.id), str(b.id)]})
    w2 = await add_workflow(uow, {"email": [str(b.id)]})
    reg = registry()
    assert await labels(reg, uow, w1) == ["A", "B"]
    assert await labels(reg, uow, w2) == ["B"]


async def test_inactive_missing_and_wrong_channel_providers_are_skipped():
    uow = InMemoryUnitOfWork()
    off = await add_provider(uow, label="off", is_active=False)
    sms = await add_provider(uow, label="sms", channel=ChannelType.SMS)
    ok = await add_provider(uow, label="ok")
    wf = await add_workflow(
        uow, {"email": [str(off.id), str(ProviderId.new()), str(sms.id), str(ok.id)]}
    )
    assert await labels(registry(), uow, wf) == ["ok"]


async def test_unusable_chain_falls_back_to_tenant_provider_then_env_default():
    uow = InMemoryUnitOfWork()
    off = await add_provider(uow, label="off", is_active=False)
    wf = await add_workflow(uow, {"email": [str(off.id)]})
    assert await labels(registry(), uow, wf) == ["default"]
    await add_provider(uow, label="tenant-default", priority=5)
    assert await labels(registry(), uow, wf) == ["tenant-default"]


async def test_workflow_without_chain_uses_tenant_then_env_default():
    uow = InMemoryUnitOfWork()
    wf = await add_workflow(uow, {})
    assert await labels(registry(), uow, wf) == ["default"]
    assert await labels(registry(), uow, None) == ["default"]


async def test_secrets_are_decrypted_into_the_built_driver():
    box = SecretBox(generate_key())
    uow = InMemoryUnitOfWork()
    p = await add_provider(
        uow, name="smtp", label="Mail",
        config={"host": "smtp.acme.io", "from_address": "a@acme.io"},
        secrets_encrypted=box.encrypt({"username": "u", "password": "s3cret"}),
    )
    wf = await add_workflow(uow, {"email": [str(p.id)]})
    [resolved] = await registry(box).resolve_chain(uow, TENANT, ChannelType.EMAIL, wf.id)
    assert resolved.provider._settings.smtp_password == "s3cret"
    assert resolved.provider._settings.smtp_host == "smtp.acme.io"


async def test_undecryptable_secrets_skip_that_provider_instead_of_crashing():
    uow = InMemoryUnitOfWork()
    bad = await add_provider(
        uow, name="smtp", label="bad", config={"host": "h"},
        secrets_encrypted=SecretBox(generate_key()).encrypt({"password": "x"}),
    )
    good = await add_provider(uow, label="good")
    wf = await add_workflow(uow, {"email": [str(bad.id), str(good.id)]})
    assert await labels(registry(), uow, wf) == ["good"]


async def test_edited_provider_rebuilds_its_driver():
    uow = InMemoryUnitOfWork()
    from datetime import datetime, timezone
    p = await add_provider(uow, name="smtp", config={"host": "one", "from_address": "a@b.c"})
    p.updated_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    reg = registry()
    [first] = await reg.resolve_chain(uow, TENANT, ChannelType.EMAIL)
    p.config = {"host": "two", "from_address": "a@b.c"}
    p.updated_at = datetime(2026, 1, 2, tzinfo=timezone.utc)
    [second] = await reg.resolve_chain(uow, TENANT, ChannelType.EMAIL)
    assert (first.provider._settings.smtp_host, second.provider._settings.smtp_host) == ("one", "two")
