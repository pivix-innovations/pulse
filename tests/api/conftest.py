"""HTTP-level harness: the real app and routers over in-memory fakes (no DB/Redis)."""
from __future__ import annotations

from dataclasses import dataclass

import httpx
import pytest

import api.v1.auth
import api.v1.sse
from api.dependencies import get_uow
from domain.entities.identity import Membership, Tenant, User
from domain.enums.identity import TenantRole
from domain.value_objects.ids import MembershipId, TenantId, UserId
from config.loader import get_settings
from main import create_app
from services.identity_service import IdentityService, hash_password
from services.metrics_service import MetricsService
from services.notification_service import NotificationService
from services.ports.clients import ResolvedRecipient
from services.rendering_service import RenderingService
from tests.fakes import InMemoryUnitOfWork, StaticSentinelClient
from workflows.registry import WorkflowRegistry

PASSWORD = "correct horse battery"


class FakeRedis:
    def __init__(self) -> None:
        self.deleted: list[str] = []

    async def delete(self, key: str) -> None:
        self.deleted.append(key)


@dataclass
class World:
    uow: InMemoryUnitOfWork
    client: httpx.AsyncClient
    tenant_id: TenantId
    api_key: str
    redis: FakeRedis

    @property
    def key_headers(self) -> dict[str, str]:
        return {"X-API-Key": self.api_key}


@pytest.fixture
async def world(monkeypatch):
    uow = InMemoryUnitOfWork()
    app = create_app()
    identity = IdentityService(lambda: uow)

    app.state.settings = get_settings()
    app.state.identity_service = identity
    app.state.notification_service = NotificationService(
        uow_factory=lambda: uow,
        sentinel=StaticSentinelClient([ResolvedRecipient(external_ref="u1", email="u1@example.com")]),
        registry=WorkflowRegistry(),
        rendering=RenderingService(),
    )
    app.state.metrics_service = MetricsService(lambda: uow)
    app.state.redis = FakeRedis()

    # Dependencies are overridden; the two routers that call get_uow() directly are patched.
    app.dependency_overrides[get_uow] = lambda: uow
    monkeypatch.setattr(api.v1.auth, "get_uow", lambda request: uow)
    monkeypatch.setattr(api.v1.sse, "get_uow", lambda request: uow)

    tenant = Tenant(id=TenantId.new(), name="Acme", slug="acme")
    await uow.tenants.add(tenant)
    user = User(id=UserId.new(), email="ada@example.com", password_hash=hash_password(PASSWORD))
    await uow.users.add(user)
    await uow.memberships.upsert(
        Membership(
            id=MembershipId.new(), tenant_id=tenant.id, user_id=user.id, role=TenantRole.ADMIN
        )
    )
    api_key, _ = await identity.create_api_key(tenant.id, "tests")

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield World(uow, client, tenant.id, api_key, app.state.redis)
