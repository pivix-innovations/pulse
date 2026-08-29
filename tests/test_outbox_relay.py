from __future__ import annotations

from config.outbox import OutboxSettings
from domain.entities.outbox import OutboxEntry
from domain.enums import OutboxStatus
from domain.value_objects.ids import OutboxEntryId, TenantId
from jobqueue.relay import OutboxRelay
from tests.fakes import FakePublisher, InMemoryUnitOfWork


def seed_entry(uow: InMemoryUnitOfWork, topic: str = "deliver.email") -> OutboxEntry:
    entry = OutboxEntry(
        id=OutboxEntryId.new(),
        tenant_id=TenantId.new(),
        aggregate_type="delivery_attempt",
        aggregate_id="a-1",
        topic=topic,
        payload={"attempt_id": "a-1"},
    )
    uow.outbox.items[str(entry.id)] = entry
    return entry


async def test_relay_publishes_and_marks_published():
    uow = InMemoryUnitOfWork()
    entry = seed_entry(uow)
    publisher = FakePublisher()
    relay = OutboxRelay(lambda: uow, publisher, OutboxSettings())

    relayed = await relay.relay_once()

    assert relayed == 1
    assert entry.status is OutboxStatus.PUBLISHED
    assert entry.published_at is not None
    topic, payload, job_id, _defer = publisher.published[0]
    assert topic == "deliver.email"
    assert job_id == f"outbox:{entry.id}"
    assert uow.commits == 1


async def test_relay_keeps_entry_pending_when_publish_fails():
    uow = InMemoryUnitOfWork()
    entry = seed_entry(uow)

    class ExplodingPublisher:
        async def enqueue(self, topic, payload, *, job_id=None):
            raise RuntimeError("redis down")

    relay = OutboxRelay(lambda: uow, ExplodingPublisher(), OutboxSettings())
    relayed = await relay.relay_once()

    assert relayed == 0
    assert entry.status is OutboxStatus.PENDING  # picked up again next cycle


async def test_relay_idle_when_nothing_pending():
    uow = InMemoryUnitOfWork()
    relay = OutboxRelay(lambda: uow, FakePublisher(), OutboxSettings())
    assert await relay.relay_once() == 0
