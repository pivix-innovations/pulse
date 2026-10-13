"""SSE fan-out on a real Redis: pub/sub across API instances and connection counting."""
from __future__ import annotations

import asyncio
import json
import os

import pytest
from redis.asyncio import Redis

from config.loader import get_settings
from domain.value_objects.ids import RecipientId, TenantId
from sse.broadcaster import SseBroadcaster
from sse.registry import SseConnectionRegistry

pytestmark = pytest.mark.skipif(not os.environ.get("PULSE_E2E"), reason="set PULSE_E2E=1 (needs Redis)")


def client(db: int = 12) -> Redis:
    r = get_settings().redis
    return Redis(host=r.host, port=r.port, db=db, password=r.password)


@pytest.fixture
async def redis():
    r = client()
    await r.flushdb()  # DB 12 belongs to this test
    try:
        yield r
    finally:
        await r.flushdb()
        await r.aclose()


async def test_an_event_published_by_one_process_reaches_a_stream_held_by_another(redis):
    tenant, recipient = TenantId.new(), RecipientId.new()
    api_instance = SseConnectionRegistry(client())          # the API process holding the connection
    worker = SseBroadcaster(client())                       # a different process delivering the notification

    async with api_instance.subscribe(tenant, recipient) as sub:
        receivers = await worker.publish(tenant, recipient, {"id": "a1", "title": "Hi", "body": "b"})
        message = await sub.get(timeout=2)
    assert receivers == 1
    assert json.loads(message) == {"id": "a1", "title": "Hi", "body": "b"}


async def test_publishing_with_nobody_connected_reports_zero_receivers(redis):
    worker = SseBroadcaster(client())
    assert await worker.publish(TenantId.new(), RecipientId.new(), {"id": "x"}) == 0


async def test_every_open_stream_of_a_recipient_receives_the_event(redis):
    tenant, recipient = TenantId.new(), RecipientId.new()
    tab_one, tab_two = SseConnectionRegistry(client()), SseConnectionRegistry(client())
    worker = SseBroadcaster(client())

    async with tab_one.subscribe(tenant, recipient) as a, tab_two.subscribe(tenant, recipient) as b:
        assert await worker.publish(tenant, recipient, {"id": "n"}) == 2
        got = await asyncio.gather(a.get(timeout=2), b.get(timeout=2))
    assert [json.loads(m)["id"] for m in got] == ["n", "n"]


async def test_events_never_leak_to_another_recipient_or_tenant(redis):
    tenant, ada, bob = TenantId.new(), RecipientId.new(), RecipientId.new()
    other_tenant = TenantId.new()
    registry = SseConnectionRegistry(client())
    worker = SseBroadcaster(client())

    async with registry.subscribe(tenant, ada) as ada_stream, registry.subscribe(other_tenant, ada) as other_stream:
        assert await worker.publish(tenant, bob, {"id": "for-bob"}) == 0          # nobody listening as bob
        assert await ada_stream.get(timeout=0.3) is None
        assert await other_stream.get(timeout=0.3) is None                         # same recipient id, other tenant
        assert await worker.publish(tenant, ada, {"id": "for-ada"}) == 1
        assert json.loads(await ada_stream.get(timeout=2))["id"] == "for-ada"
        assert await other_stream.get(timeout=0.3) is None


async def test_connection_count_tracks_open_streams_and_cleans_up(redis):
    tenant, recipient = TenantId.new(), RecipientId.new()
    registry = SseConnectionRegistry(client())
    assert await registry.connection_count(tenant, recipient) == 0
    async with registry.subscribe(tenant, recipient):
        assert await registry.connection_count(tenant, recipient) == 1
        async with registry.subscribe(tenant, recipient):
            assert await registry.connection_count(tenant, recipient) == 2
        assert await registry.connection_count(tenant, recipient) == 1
    assert await registry.connection_count(tenant, recipient) == 0
    assert await redis.keys("pulse:sse:conn:*") == []                              # no key left behind


async def test_the_counter_has_a_ttl_so_a_crashed_instance_cannot_leak_it_forever(redis):
    tenant, recipient = TenantId.new(), RecipientId.new()
    registry = SseConnectionRegistry(client(), counter_ttl_seconds=60)
    await registry.register(tenant, recipient)            # connection opened, process then dies: never unregistered
    ttl = await redis.ttl(f"pulse:sse:conn:{tenant}:{recipient}")
    assert 0 < ttl <= 60


async def test_a_stream_closed_by_an_error_still_releases_its_subscription(redis):
    tenant, recipient = TenantId.new(), RecipientId.new()
    registry = SseConnectionRegistry(client())
    worker = SseBroadcaster(client())
    with pytest.raises(RuntimeError):
        async with registry.subscribe(tenant, recipient):
            raise RuntimeError("client handler blew up")
    assert await registry.connection_count(tenant, recipient) == 0
    assert await worker.publish(tenant, recipient, {"id": "x"}) == 0               # unsubscribed


async def test_an_idle_get_waits_the_full_timeout_instead_of_returning_early(redis):
    """redis-py returns None the moment it swallows the subscribe confirmation; we must not."""
    import time

    registry = SseConnectionRegistry(client())
    async with registry.subscribe(TenantId.new(), RecipientId.new()) as sub:
        started = time.monotonic()
        assert await sub.get(timeout=0.5) is None
        assert time.monotonic() - started >= 0.45
