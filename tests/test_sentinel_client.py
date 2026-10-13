from __future__ import annotations

import asyncio

import httpx
import pytest

from clients.sentinel_client import DatabaseRecipientClient, build_recipient_client
from clients.sentinel_http import HttpSentinelClient, ResilientSentinelClient
from config.sentinel import SentinelSettings
from domain.entities.recipient import Recipient
from domain.value_objects.ids import RecipientId, TenantId
from services.ports.clients import ResolvedRecipient, SentinelUnavailable
from tests.fakes import InMemoryUnitOfWork

TENANT = TenantId.new()
ADA = {"external_ref": "ada", "email": "ada@example.com", "phone": "+1555", "locale": "fr", "timezone": "Europe/Paris"}


def settings(**kw):
    return SentinelSettings(**{"base_url": "http://sentinel.test", "token": "s3cret", "retries": 2, **kw})


def mock(handler):
    calls: list[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return handler(request)

    return httpx.MockTransport(wrapped), calls


def ok(*recipients):
    return lambda request: httpx.Response(200, json={"recipients": list(recipients)})


# ── the HTTP call ──────────────────────────────────────────────────────


async def test_request_follows_the_contract_and_parses_recipients():
    transport, calls = mock(ok(ADA))
    client = HttpSentinelClient(settings(), transport)
    got = await client.resolve_recipients(TENANT, "user.signup", {"user_id": "ada", "plan": "pro", "recipients": ["x"]})
    assert got == [ResolvedRecipient("ada", "ada@example.com", "+1555", "fr", "Europe/Paris")]
    [req] = calls
    assert req.method == "POST" and str(req.url) == "http://sentinel.test/v1/recipients/resolve"
    assert req.headers["authorization"] == "Bearer s3cret" and req.headers["x-pulse-tenant-id"] == str(TENANT)
    import json

    assert json.loads(req.content) == {
        "tenant_id": str(TENANT), "event_type": "user.signup", "payload": {"user_id": "ada", "plan": "pro"},
    }


async def test_only_the_configured_payload_keys_are_forwarded():
    transport, calls = mock(ok(ADA))
    client = HttpSentinelClient(settings(payload_keys=["user_id"]), transport)
    await client.resolve_recipients(TENANT, "e", {"user_id": "ada", "credit_card": "4242"})
    import json

    assert json.loads(calls[0].content)["payload"] == {"user_id": "ada"}


async def test_missing_fields_get_defaults_and_malformed_recipients_are_skipped():
    transport, _ = mock(ok({"external_ref": "bob"}, {"email": "no-ref@x.io"}, "junk"))
    got = await HttpSentinelClient(settings(), transport).resolve_recipients(TENANT, "e", {})
    assert got == [ResolvedRecipient("bob", None, None, "en", "UTC")]


async def test_404_means_nobody_which_is_a_real_answer():
    transport, _ = mock(lambda r: httpx.Response(404))
    assert await HttpSentinelClient(settings(), transport).resolve_recipients(TENANT, "e", {}) == []


@pytest.mark.parametrize("status, retryable", [(500, True), (503, True), (429, True), (401, False), (403, False), (400, False)])
async def test_failures_are_unavailable_with_the_right_retry_hint(status, retryable):
    transport, _ = mock(lambda r: httpx.Response(status))
    with pytest.raises(SentinelUnavailable) as err:
        await HttpSentinelClient(settings(), transport).resolve_recipients(TENANT, "e", {})
    assert err.value.retryable is retryable


async def test_network_errors_and_timeouts_are_unavailable():
    def boom(request):
        raise httpx.ConnectTimeout("slow")

    transport, _ = mock(boom)
    with pytest.raises(SentinelUnavailable, match="unreachable"):
        await HttpSentinelClient(settings(), transport).resolve_recipients(TENANT, "e", {})


@pytest.mark.parametrize("body", [{"nope": 1}, {"recipients": "ada"}, "not json"])
async def test_unusable_responses_are_rejected_not_treated_as_nobody(body):
    transport, _ = mock(lambda r: httpx.Response(200, json=body) if not isinstance(body, str) else httpx.Response(200, text=body))
    with pytest.raises(SentinelUnavailable) as err:
        await HttpSentinelClient(settings(), transport).resolve_recipients(TENANT, "e", {})
    assert err.value.retryable is False


async def test_an_oversized_answer_is_rejected_instead_of_silently_truncated():
    transport, _ = mock(ok(*({"external_ref": f"u{i}"} for i in range(11))))
    with pytest.raises(SentinelUnavailable, match="limit"):
        await HttpSentinelClient(settings(max_recipients=10), transport).resolve_recipients(TENANT, "e", {})


# ── resilience ─────────────────────────────────────────────────────────


class Clock:
    def __init__(self):
        self.now = 1000.0
        self.slept: list[float] = []

    def __call__(self):
        return self.now

    async def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


class Scripted:
    """An inner client that answers from a script of values / exceptions."""

    def __init__(self, *script):
        self.script = list(script)
        self.calls = 0

    async def resolve_recipients(self, tenant_id, event_type, payload):
        self.calls += 1
        item = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if isinstance(item, Exception):
            raise item
        return item


def resilient(inner, **kw):
    clock = Clock()
    return ResilientSentinelClient(inner, settings(**kw), clock=clock, sleep=clock.sleep), clock


A = [ResolvedRecipient("ada")]


async def test_answers_are_cached_for_the_ttl_then_refreshed():
    inner = Scripted(A)
    client, clock = resilient(inner, cache_ttl_seconds=60)
    for _ in range(3):
        assert await client.resolve_recipients(TENANT, "e", {"user_id": "ada"}) == A
    assert inner.calls == 1
    clock.now += 61
    await client.resolve_recipients(TENANT, "e", {"user_id": "ada"})
    assert inner.calls == 2


async def test_the_cache_is_keyed_by_tenant_event_and_payload():
    inner = Scripted(A)
    client, _ = resilient(inner)
    await client.resolve_recipients(TENANT, "e", {"user_id": "ada"})
    await client.resolve_recipients(TENANT, "e", {"user_id": "bob"})
    await client.resolve_recipients(TENANT, "other", {"user_id": "ada"})
    await client.resolve_recipients(TenantId.new(), "e", {"user_id": "ada"})
    assert inner.calls == 4                                    # nobody's answer leaks to someone else


async def test_nobody_is_cached_for_a_shorter_time():
    inner = Scripted([])
    client, clock = resilient(inner, cache_ttl_seconds=60, negative_cache_ttl_seconds=10)
    await client.resolve_recipients(TENANT, "e", {"x": 1})
    await client.resolve_recipients(TENANT, "e", {"x": 1})
    assert inner.calls == 1
    clock.now += 11
    await client.resolve_recipients(TENANT, "e", {"x": 1})
    assert inner.calls == 2


async def test_transient_failures_are_retried_with_backoff_then_succeed():
    inner = Scripted(SentinelUnavailable("blip"), SentinelUnavailable("blip"), A)
    client, clock = resilient(inner, retries=2, retry_backoff_seconds=1.0)
    assert await client.resolve_recipients(TENANT, "e", {}) == A
    assert inner.calls == 3 and len(clock.slept) == 2
    assert 0.5 <= clock.slept[0] <= 1.0 and 1.0 <= clock.slept[1] <= 2.0     # exponential, jittered


async def test_non_retryable_failures_are_not_retried():
    inner = Scripted(SentinelUnavailable("bad token", retryable=False))
    client, clock = resilient(inner)
    with pytest.raises(SentinelUnavailable):
        await client.resolve_recipients(TENANT, "e", {})
    assert inner.calls == 1 and clock.slept == []


async def test_when_sentinel_is_down_the_last_good_answer_is_served():
    inner = Scripted(A, SentinelUnavailable("down"))
    client, clock = resilient(inner, cache_ttl_seconds=60, stale_ttl_seconds=3600, retries=0)
    assert await client.resolve_recipients(TENANT, "e", {}) == A
    clock.now += 120                                            # cache expired, Sentinel now failing
    assert await client.resolve_recipients(TENANT, "e", {}) == A       # stale-if-error
    clock.now += 4000                                           # ...but not forever
    with pytest.raises(SentinelUnavailable):
        await client.resolve_recipients(TENANT, "e", {})


async def test_with_nothing_cached_an_outage_raises_rather_than_returning_nobody():
    client, _ = resilient(Scripted(SentinelUnavailable("down")), retries=0)
    with pytest.raises(SentinelUnavailable):
        await client.resolve_recipients(TENANT, "e", {})


async def test_the_circuit_opens_after_consecutive_failures_then_probes_after_the_cooldown():
    inner = Scripted(SentinelUnavailable("down"))
    client, clock = resilient(inner, retries=0, breaker_failure_threshold=3, breaker_cooldown_seconds=30)
    for i in range(3):
        with pytest.raises(SentinelUnavailable):
            await client.resolve_recipients(TENANT, "e", {"i": i})
    assert inner.calls == 3 and client.circuit_open
    with pytest.raises(SentinelUnavailable, match="circuit open"):
        await client.resolve_recipients(TENANT, "e", {"i": 99})
    assert inner.calls == 3                                     # not even attempted
    clock.now += 31
    inner.script = [A]
    assert await client.resolve_recipients(TENANT, "e", {"i": 100}) == A
    assert not client.circuit_open


async def test_a_success_resets_the_failure_count():
    inner = Scripted(SentinelUnavailable("x"), A, SentinelUnavailable("x"), SentinelUnavailable("x"))
    client, _ = resilient(inner, retries=0, breaker_failure_threshold=3)
    for payload in ({"i": 1}, {"i": 2}):
        try:
            await client.resolve_recipients(TENANT, "e", payload)
        except SentinelUnavailable:
            pass
    assert not client.circuit_open


async def test_concurrent_identical_lookups_share_one_call():
    gate = asyncio.Event()

    class Slow:
        calls = 0

        async def resolve_recipients(self, *a):
            Slow.calls += 1
            await gate.wait()
            return A

    client, _ = resilient(Slow())
    tasks = [asyncio.create_task(client.resolve_recipients(TENANT, "e", {"u": 1})) for _ in range(20)]
    await asyncio.sleep(0)
    gate.set()
    assert all(r == A for r in await asyncio.gather(*tasks))
    assert Slow.calls == 1


async def test_concurrent_lookups_all_see_the_same_failure():
    gate = asyncio.Event()

    class Failing:
        async def resolve_recipients(self, *a):
            await gate.wait()
            raise SentinelUnavailable("down", retryable=False)

    client, _ = resilient(Failing())
    tasks = [asyncio.create_task(client.resolve_recipients(TENANT, "e", {})) for _ in range(5)]
    await asyncio.sleep(0)
    gate.set()
    results = await asyncio.gather(*tasks, return_exceptions=True)
    assert all(isinstance(r, SentinelUnavailable) for r in results)


# ── how the layers combine ─────────────────────────────────────────────


class FakeRemote:
    def __init__(self, result):
        self.result, self.calls = result, 0

    async def resolve_recipients(self, *a):
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


async def local_uow(*refs):
    uow = InMemoryUnitOfWork()
    for ref in refs:
        await uow.recipients.upsert_by_external_ref(
            Recipient(id=RecipientId.new(), tenant_id=TENANT, external_ref=ref, email=f"{ref}@local.test"))
    return uow


async def test_inline_recipients_never_call_sentinel():
    remote = FakeRemote(A)
    client = DatabaseRecipientClient(lambda: InMemoryUnitOfWork(), remote=remote)
    got = await client.resolve_recipients(TENANT, "e", {"recipients": [{"external_ref": "inline"}]})
    assert [r.external_ref for r in got] == ["inline"] and remote.calls == 0


async def test_sentinel_is_authoritative_even_when_it_says_nobody():
    uow = await local_uow("ada")
    remote = FakeRemote([])
    client = DatabaseRecipientClient(lambda: uow, remote=remote)
    assert await client.resolve_recipients(TENANT, "e", {"user_id": "ada"}) == []   # a stale local row must not resurrect them


async def test_an_outage_falls_back_to_the_local_snapshot():
    uow = await local_uow("ada")
    client = DatabaseRecipientClient(lambda: uow, remote=FakeRemote(SentinelUnavailable("down")))
    [got] = await client.resolve_recipients(TENANT, "e", {"user_id": "ada"})
    assert got.external_ref == "ada" and got.email == "ada@local.test"


async def test_an_outage_with_no_local_match_propagates_so_the_caller_can_retry():
    uow = await local_uow()
    client = DatabaseRecipientClient(lambda: uow, remote=FakeRemote(SentinelUnavailable("down")))
    with pytest.raises(SentinelUnavailable):
        await client.resolve_recipients(TENANT, "e", {"user_id": "stranger"})


async def test_without_sentinel_configured_it_is_the_old_local_behaviour():
    uow = await local_uow("ada")
    from config.loader import get_settings

    settings_ = get_settings()
    assert settings_.sentinel.base_url is None
    client = build_recipient_client(lambda: uow, settings_)
    assert [r.external_ref for r in await client.resolve_recipients(TENANT, "e", {"user_id": "ada"})] == ["ada"]
    assert await client.resolve_recipients(TENANT, "e", {}) == []


async def test_building_with_a_base_url_wires_the_resilient_remote():
    from config.loader import get_settings

    configured = get_settings().model_copy(update={"sentinel": SentinelSettings(base_url="http://sentinel.test")})
    client = build_recipient_client(lambda: InMemoryUnitOfWork(), configured)
    assert isinstance(client._remote, ResilientSentinelClient)
