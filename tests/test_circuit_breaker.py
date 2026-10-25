from __future__ import annotations


from config.breaker import BreakerSettings
from config.retry import RetrySettings
from domain.enums import ChannelType
from domain.value_objects.ids import ProviderId, TenantId
from jobqueue.circuit_breaker import RedisCircuitBreaker
from services.delivery_service import DeliveryService
from services.ports.providers import ProviderSendError
from tests.fakes import (
    FakeDispatchGuard, FakeProvider, FakeProviderRegistry, FakePublisher, InMemoryUnitOfWork,
)
from tests.test_delivery_service import seed_attempt


class FakeRedis:
    """Just the commands the breaker uses, with TTLs on a controllable clock."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.data: dict[str, tuple[str, float | None]] = {}

    def _live(self, key):
        item = self.data.get(key)
        if item and item[1] is not None and item[1] <= self.now:
            del self.data[key]
            return None
        return item

    async def get(self, key):
        item = self._live(key)
        return item[0] if item else None

    async def exists(self, key):
        return 1 if self._live(key) else 0

    async def set(self, key, value, nx=False, ex=None):
        if nx and self._live(key):
            return None
        self.data[key] = (value, self.now + ex if ex else None)
        return True

    async def incr(self, key):
        item = self._live(key)
        n = int(item[0]) + 1 if item else 1
        self.data[key] = (str(n), item[1] if item else None)
        return n

    async def expire(self, key, seconds):
        item = self._live(key)
        if item:
            self.data[key] = (item[0], self.now + seconds)

    async def delete(self, *keys):
        for k in keys:
            self.data.pop(k, None)


SETTINGS = BreakerSettings(failure_threshold=3, window_seconds=300, cooldown_seconds=60, probe_lease_seconds=30)
PID = ProviderId.new()


def make():
    redis = FakeRedis()
    return redis, RedisCircuitBreaker(redis, SETTINGS)


async def fail(cb, n):
    for _ in range(n):
        await cb.record_failure(PID)


async def test_closed_until_the_threshold_is_reached():
    _, cb = make()
    await fail(cb, 2)
    assert await cb.state(PID) == "closed" and await cb.allow(PID)
    await fail(cb, 1)
    assert await cb.state(PID) == "open" and not await cb.allow(PID)


async def test_a_success_in_between_resets_the_count():
    _, cb = make()
    await fail(cb, 2)
    await cb.record_success(PID)
    await fail(cb, 2)
    assert await cb.state(PID) == "closed"


async def test_cooldown_then_exactly_one_probe():
    redis, cb = make()
    await fail(cb, 3)
    redis.now += 61  # cooldown over
    assert await cb.state(PID) == "half_open"
    assert await cb.allow(PID) is True       # the probe
    assert await cb.allow(PID) is False      # everyone else waits for its verdict


async def test_probe_success_closes_the_circuit():
    redis, cb = make()
    await fail(cb, 3)
    redis.now += 61
    assert await cb.allow(PID)
    await cb.record_success(PID)
    assert await cb.state(PID) == "closed" and await cb.allow(PID) and await cb.allow(PID)


async def test_probe_failure_reopens_for_another_cooldown():
    redis, cb = make()
    await fail(cb, 3)
    redis.now += 61
    assert await cb.allow(PID)
    await cb.record_failure(PID)
    assert await cb.state(PID) == "open"
    redis.now += 30
    assert not await cb.allow(PID)
    redis.now += 31
    assert await cb.allow(PID)


async def test_a_lost_probe_does_not_wedge_the_circuit():
    redis, cb = make()
    await fail(cb, 3)
    redis.now += 61
    assert await cb.allow(PID)        # probe worker dies without reporting
    redis.now += 31                   # its lease expires
    assert await cb.allow(PID)        # a new probe is allowed


async def test_old_failures_do_not_count_as_consecutive():
    redis, cb = make()
    await fail(cb, 2)
    redis.now += 301                  # outside the window
    await fail(cb, 1)
    assert await cb.state(PID) == "closed"


async def test_providers_are_independent():
    _, cb = make()
    other = ProviderId.new()
    await fail(cb, 3)
    assert await cb.allow(other)


async def test_disabled_breaker_never_blocks():
    redis = FakeRedis()
    cb = RedisCircuitBreaker(redis, BreakerSettings(enabled=False))
    for _ in range(10):
        await cb.record_failure(PID)
    assert await cb.allow(PID)


# ── in the delivery path ───────────────────────────────────────────────


class ScriptedBreaker:
    def __init__(self, open_ids=()):
        self.open = set(open_ids)
        self.failures: list = []
        self.successes: list = []

    async def allow(self, pid):
        return pid not in self.open

    async def record_failure(self, pid):
        self.failures.append(pid)

    async def record_success(self, pid):
        self.successes.append(pid)

    async def state(self, pid):
        return "open" if pid in self.open else "closed"


async def test_open_provider_is_skipped_without_being_called_or_timed():
    first, second = FakeProvider(), FakeProvider()
    reg = FakeProviderRegistry([first, second])
    breaker = ScriptedBreaker(open_ids={reg.chain[0].provider_id})
    uow = InMemoryUnitOfWork()
    tenant = TenantId.new()
    attempt = seed_attempt(uow, tenant, ChannelType.EMAIL)
    service = DeliveryService(
        uow_factory=lambda: uow, providers=reg, guard=FakeDispatchGuard(),
        publisher=FakePublisher(), retry=RetrySettings(), breaker=breaker,
    )
    assert await service.deliver(tenant, attempt.id) == "sent"
    assert not first.sent and len(second.sent) == 1
    stored = uow.delivery_attempts.items[str(attempt.id)]
    assert "p0: skipped (circuit open)" in stored.last_error


async def test_when_every_circuit_is_open_the_chain_is_tried_anyway():
    a, b = FakeProvider(), FakeProvider()
    reg = FakeProviderRegistry([a, b])
    breaker = ScriptedBreaker(open_ids={r.provider_id for r in reg.chain})
    uow = InMemoryUnitOfWork()
    tenant = TenantId.new()
    attempt = seed_attempt(uow, tenant, ChannelType.EMAIL)
    service = DeliveryService(
        uow_factory=lambda: uow, providers=reg, guard=FakeDispatchGuard(),
        publisher=FakePublisher(), retry=RetrySettings(), breaker=breaker,
    )
    assert await service.deliver(tenant, attempt.id) == "sent"
    assert len(a.sent) == 1  # no blackout


async def test_outcomes_feed_the_breaker_but_bad_recipients_do_not():
    reg = FakeProviderRegistry([FakeProvider(fail=True), FakeProvider()])
    breaker = ScriptedBreaker()
    uow = InMemoryUnitOfWork()
    tenant = TenantId.new()
    attempt = seed_attempt(uow, tenant, ChannelType.EMAIL)
    service = DeliveryService(
        uow_factory=lambda: uow, providers=reg, guard=FakeDispatchGuard(),
        publisher=FakePublisher(), retry=RetrySettings(), breaker=breaker,
    )
    await service.deliver(tenant, attempt.id)
    assert breaker.failures == [reg.chain[0].provider_id]
    assert breaker.successes == [reg.chain[1].provider_id]

    bad = FakeProviderRegistry([
        FakeProvider(error=ProviderSendError("bad number", retryable=False, failover=False)),
        FakeProvider(),
    ])
    breaker2 = ScriptedBreaker()
    uow2 = InMemoryUnitOfWork()
    attempt2 = seed_attempt(uow2, tenant, ChannelType.EMAIL)
    service2 = DeliveryService(
        uow_factory=lambda: uow2, providers=bad, guard=FakeDispatchGuard(),
        publisher=FakePublisher(), retry=RetrySettings(), breaker=breaker2,
    )
    await service2.deliver(tenant, attempt2.id)
    assert breaker2.failures == [] and breaker2.successes == []


async def test_a_broken_breaker_never_fails_a_delivery():
    class Exploding(ScriptedBreaker):
        async def record_success(self, pid):
            raise ConnectionError("redis down")

    reg = FakeProviderRegistry([FakeProvider(), FakeProvider()])
    uow = InMemoryUnitOfWork()
    tenant = TenantId.new()
    attempt = seed_attempt(uow, tenant, ChannelType.EMAIL)
    service = DeliveryService(
        uow_factory=lambda: uow, providers=reg, guard=FakeDispatchGuard(),
        publisher=FakePublisher(), retry=RetrySettings(), breaker=Exploding(),
    )
    assert await service.deliver(tenant, attempt.id) == "sent"
