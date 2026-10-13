"""
Repository contract tests: the same assertions run against the in-memory fakes
(always) and the real Postgres repositories (with PULSE_PG_TESTS=1), so the
fakes the rest of the suite relies on are checked against the real thing.

    docker compose up -d db
    PULSE_PG_TESTS=1 uv run pytest tests/repositories -v
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import asyncpg
import pytest

from config.loader import get_settings
from database.database import Database
from domain.entities.delivery import DeliveryAttempt
from domain.entities.event import Event
from domain.entities.notification import Notification
from domain.entities.provider import Provider
from domain.entities.recipient import Recipient
from domain.entities.workflow import Workflow
from domain.enums import ChannelType
from domain.value_objects.ids import (
    DeliveryAttemptId, EventId, NotificationId, ProviderId, RecipientId, TenantId, WorkflowId,
)
from repositories.uow import SqlAlchemyUnitOfWork
from tests.fakes import InMemoryUnitOfWork

ROOT = Path(__file__).resolve().parents[2]
PG_ENABLED = bool(os.environ.get("PULSE_PG_TESTS"))
DB_NAME = "pulse_repo_test"

requires_pg = pytest.mark.skipif(not PG_ENABLED, reason="set PULSE_PG_TESTS=1 (needs Postgres)")


@dataclass
class Env:
    backend: str
    uow: Callable[[], object]  # async context manager yielding a UnitOfWork

    @property
    def is_pg(self) -> bool:
        return self.backend == "postgres"


@pytest.fixture(scope="session")
def pg_database() -> str | None:
    """A throwaway database migrated with the real Alembic revisions (None when disabled)."""
    if not PG_ENABLED:
        return None
    db = get_settings().database

    async def create() -> None:
        admin = await asyncpg.connect(
            host=db.host, port=db.port, user=db.username, password=db.password, database="postgres"
        )
        try:
            await admin.execute(f'DROP DATABASE IF EXISTS "{DB_NAME}" WITH (FORCE)')
            await admin.execute(f'CREATE DATABASE "{DB_NAME}"')
        finally:
            await admin.close()

    # Run on a private thread: this fixture can be resolved from inside a running loop.
    import threading

    errors: list[BaseException] = []

    def run() -> None:
        try:
            asyncio.run(create())
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    t = threading.Thread(target=run)
    t.start()
    t.join()
    if errors:
        raise errors[0]
    out = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT, capture_output=True, text=True,
        env={**os.environ, "PULSE_DATABASE__DB": DB_NAME},
    )
    assert out.returncode == 0, out.stderr
    return DB_NAME


async def _pg_env(database_name: str):
    from sqlalchemy import text

    settings = get_settings().database.model_copy(update={"db": database_name})
    database = Database(settings)
    await database.initialize()
    async with database.engine.begin() as conn:  # clean slate for every test
        tables = (await conn.execute(text(
            "SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename <> 'alembic_version'"
        ))).scalars().all()
        await conn.execute(text("TRUNCATE " + ", ".join(f'"{t}"' for t in tables) + " CASCADE"))
    return database


@pytest.fixture(params=["memory", pytest.param("postgres", marks=requires_pg)])
async def env(request, pg_database):
    if request.param == "memory":
        uow = InMemoryUnitOfWork()
        yield Env("memory", lambda: uow)
        return
    database = await _pg_env(pg_database)
    try:
        yield Env("postgres", lambda: SqlAlchemyUnitOfWork(database.session_factory))
    finally:
        await database.shutdown()


@pytest.fixture
async def pg_env(pg_database):
    """Postgres only (concurrency / constraint tests that have no in-memory meaning)."""
    if pg_database is None:
        pytest.skip("set PULSE_PG_TESTS=1 (needs Postgres)")
    database = await _pg_env(pg_database)
    try:
        yield Env("postgres", lambda: SqlAlchemyUnitOfWork(database.session_factory))
    finally:
        await database.shutdown()


# ── seeding ────────────────────────────────────────────────────────────


@dataclass
class Graph:
    tenant: TenantId
    recipient: Recipient
    notification: Notification
    provider: Provider


async def seed(env: Env, tenant: TenantId | None = None) -> Graph:
    """The parents an attempt needs (Postgres enforces the foreign keys)."""
    tenant = tenant or TenantId.new()
    async with env.uow() as uow:
        event = Event(id=EventId.new(), tenant_id=tenant, event_type="t.e", source="test",
                      idempotency_key=str(uuid.uuid4()))
        await uow.events.add(event)
        workflow = Workflow(id=WorkflowId.new(), tenant_id=tenant, name=f"wf-{uuid.uuid4().hex[:6]}", event_type="t.e")
        await uow.workflows.add(workflow)
        recipient = await uow.recipients.upsert_by_external_ref(
            Recipient(id=RecipientId.new(), tenant_id=tenant, external_ref=f"u-{uuid.uuid4().hex[:6]}")
        )
        notification = Notification(id=NotificationId.new(), tenant_id=tenant, event_id=event.id, workflow_id=workflow.id)
        await uow.notifications.add(notification)
        provider = Provider(
            id=ProviderId.new(), tenant_id=tenant, channel=ChannelType.EMAIL, name="console",
            label=f"p-{uuid.uuid4().hex[:6]}",
        )
        await uow.providers.add(provider)
        await uow.commit()
    return Graph(tenant, recipient, notification, provider)


def make_attempt(g: Graph, channel: ChannelType = ChannelType.EMAIL, **fields) -> DeliveryAttempt:
    return DeliveryAttempt(
        id=DeliveryAttemptId.new(), tenant_id=g.tenant, notification_id=g.notification.id,
        recipient_id=g.recipient.id, channel=channel, subject="s", body="b", **fields,
    )
