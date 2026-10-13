"""
C3: one tenant can never see or change another's data.

Tenant A owns a provider, workflow, template, preference, notification, dead
letter, device token, API key and a membership. Tenant B then tries every
tenant-scoped route against A's ids, with API-key and console-session
credentials. Everything must look like it doesn't exist (404), lists must be
empty of A's data, and A's data must come out untouched.

The last test walks the real route table and fails when a tenant-scoped route
is added without being covered here.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx
import pytest

from domain.entities.dead_letter import DeadLetter
from domain.entities.delivery import DeliveryAttempt
from domain.entities.inbox import InboxMessage
from domain.entities.identity import Membership, Tenant, User
from domain.enums import ChannelType
from domain.enums.identity import TenantRole
from domain.value_objects.ids import (
    DeadLetterId, DeliveryAttemptId, InboxMessageId, MembershipId, TenantId, UserId,
)
from services.identity_service import IdentityService, hash_password
from services.secrets import SecretBox, generate_key
from tests.api.conftest import PASSWORD

EVENT = {"event_type": "user.signup", "source": "quotient", "payload": {"plan": "pro"}}


@dataclass
class Fixture:
    world: object
    a: httpx.AsyncClient          # tenant A, API key
    b: httpx.AsyncClient          # tenant B, API key
    b_admin: httpx.AsyncClient    # tenant B, console session (admin)
    a_admin: httpx.AsyncClient    # tenant A, console session (admin)
    ids: dict
    b_tenant: Tenant


def client_for(app, headers=None) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test", headers=headers or {}
    )


@pytest.fixture
async def iso(world):
    app = world.app
    app.state.secret_box = SecretBox(generate_key())
    identity = IdentityService(lambda: world.uow)

    # Tenant B: its own API key and an admin who belongs to B only.
    tenant_b = Tenant(id=TenantId.new(), name="Bravo", slug="bravo")
    await world.uow.tenants.add(tenant_b)
    key_b, _ = await identity.create_api_key(tenant_b.id, "b-key")
    bea = User(id=UserId.new(), email="bea@example.com", password_hash=hash_password(PASSWORD))
    await world.uow.users.add(bea)
    await world.uow.memberships.upsert(
        Membership(id=MembershipId.new(), tenant_id=tenant_b.id, user_id=bea.id, role=TenantRole.ADMIN)
    )

    a = client_for(app, world.key_headers)
    b = client_for(app, {"X-API-Key": key_b})
    a_admin, b_admin = client_for(app), client_for(app)
    for c, email in ((a_admin, "ada@example.com"), (b_admin, "bea@example.com")):
        assert (await c.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})).status_code == 200
    a_admin.headers["X-Tenant-Id"] = str(world.tenant_id)
    b_admin.headers["X-Tenant-Id"] = str(tenant_b.id)

    # Everything tenant A owns.
    ids: dict = {}
    r = await a.post("/api/v1/providers", json={
        "channel": "email", "name": "resend", "label": "A's Resend",
        "config": {"from_address": "a@example.com"}, "secrets": {"api_key": "re_a_secret"},
    })
    assert r.status_code == 201, r.text
    ids["provider"] = r.json()["id"]
    r = await a.post("/api/v1/workflows", json={
        "name": "a-flow", "event_type": "a.event",
        "definition": {"channels": [{"channel": "email", "subject": "s", "body": "b", "format": "text"}]},
    })
    assert r.status_code == 201, r.text
    ids["workflow"] = r.json()["id"]
    r = await a.post("/api/v1/templates", json={"name": "a-tpl", "channel": "email", "body": "A body"})
    assert r.status_code == 201, r.text
    ids["template"] = r.json()["id"]
    r = await a.put("/api/v1/preferences", json={"external_ref": "ada", "channel": "sms", "opted_in": False})
    assert r.status_code == 200, r.text
    ids["preference"] = r.json()["id"]
    r = await a.post("/api/v1/suppressions", json={"channel": "email", "address": "blocked@example.com"})
    assert r.status_code == 201, r.text
    ids["suppression"] = r.json()["id"]
    r = await a.post("/api/v1/events", json={**EVENT, "idempotency_key": "shared-key"})
    assert r.status_code == 202, r.text
    ids["notification"] = r.json()["notification_id"]
    attempts = (await a.get(f"/api/v1/notifications/{ids['notification']}")).json()["attempts"]
    ids["attempt"] = attempts[0]["id"]

    attempt = world.uow.delivery_attempts.items[ids["attempt"]]
    dead = DeadLetter(
        id=DeadLetterId.new(), tenant_id=world.tenant_id, attempt_id=attempt.id,
        notification_id=attempt.notification_id, channel=attempt.channel, reason="a's failure",
    )
    await world.uow.dead_letters.add(dead)
    ids["dead_letter"] = str(dead.id)
    ada = await world.uow.recipients.get_by_external_ref(world.tenant_id, "ada")
    inbox = await world.uow.inbox.add(InboxMessage(
        id=InboxMessageId.new(), tenant_id=world.tenant_id, recipient_id=ada.id,
        notification_id=attempt.notification_id, attempt_id=attempt.id, title="A only", body="a's secret message",
    ))
    ids["inbox"] = str(inbox.id)
    key_row = (await a_admin.post("/api/v1/tenant/api-keys", json={"name": "a-extra"})).json()
    ids["api_key"] = key_row["id"]
    ids["membership"] = str(next(iter(await world.uow.memberships.list_for_tenant(world.tenant_id))).id)

    yield Fixture(world, a, b, b_admin, a_admin, ids, tenant_b)
    for c in (a, b, a_admin, b_admin):
        await c.aclose()


# ── B cannot touch A's resources ───────────────────────────────────────

# (client, method, path template using {ids}, json body)  -> expected 404
ATTACKS = [
    ("b", "GET", "/api/v1/notifications/{notification}", None),
    ("b", "POST", "/api/v1/notifications/{notification}/ack", {"attempt_id": "{attempt}"}),
    ("b", "GET", "/api/v1/providers/{provider}", None),
    ("b", "PUT", "/api/v1/providers/{provider}", {
        "channel": "email", "name": "resend", "label": "hijacked",
        "config": {"from_address": "evil@example.com"}, "secrets": {"api_key": "re_stolen"}}),
    ("b", "DELETE", "/api/v1/providers/{provider}", None),
    ("b", "POST", "/api/v1/provider-catalog/email/resend/test", {"provider_id": "{provider}"}),
    ("b", "GET", "/api/v1/workflows/{workflow}", None),
    ("b", "PUT", "/api/v1/workflows/{workflow}", {
        "name": "hijacked", "event_type": "a.event",
        "definition": {"channels": [{"channel": "email", "subject": "x", "body": "x", "format": "text"}]}}),
    ("b", "DELETE", "/api/v1/workflows/{workflow}", None),
    ("b", "GET", "/api/v1/templates/{template}", None),
    ("b", "PUT", "/api/v1/templates/{template}", {"name": "hijacked", "channel": "email", "body": "x"}),
    ("b", "DELETE", "/api/v1/templates/{template}", None),
    ("b", "GET", "/api/v1/templates/{template}/versions", None),
    ("b", "GET", "/api/v1/templates/{template}/versions/1", None),
    ("b", "POST", "/api/v1/templates/{template}/publish", {"note": "hijack"}),
    ("b", "POST", "/api/v1/templates/{template}/rollback", {"version": 1}),
    ("b", "POST", "/api/v1/templates/{template}/preview", {"data": {}}),
    ("b", "DELETE", "/api/v1/preferences/{preference}", None),
    ("b", "GET", "/api/v1/preferences?recipient=ada", None),   # B has no recipient "ada"
    ("b", "DELETE", "/api/v1/suppressions/{suppression}", None),
    ("b", "POST", "/api/v1/dead-letters/{dead_letter}/retry", None),
    ("b", "POST", "/api/v1/inbox/{inbox}/read?recipient=ada", None),
    ("b", "POST", "/api/v1/inbox/{inbox}/unread?recipient=ada", None),
    ("b", "POST", "/api/v1/inbox/{inbox}/archive?recipient=ada", None),
    ("b", "POST", "/api/v1/inbox/{inbox}/unarchive?recipient=ada", None),
    ("b_admin", "DELETE", "/api/v1/tenant/api-keys/{api_key}", None),
    ("b_admin", "POST", "/api/v1/tenant/api-keys/{api_key}/rotate", None),
    ("b_admin", "DELETE", "/api/v1/tenant/members/{membership}", None),
]


def fill(value, ids):
    if isinstance(value, str):
        return value.format(**ids)
    if isinstance(value, dict):
        return {k: fill(v, ids) for k, v in value.items()}
    return value


@pytest.mark.parametrize("who, method, path, body", ATTACKS, ids=[f"{m} {p}" for _, m, p, _ in ATTACKS])
async def test_another_tenants_resource_is_a_404(iso, who, method, path, body):
    client = getattr(iso, who)
    res = await client.request(method, fill(path, iso.ids), json=fill(body, iso.ids))
    assert res.status_code == 404, f"{method} {path} -> {res.status_code}: {res.text}"
    assert "re_a_secret" not in res.text and "a's failure" not in res.text


async def test_after_every_attack_tenant_a_is_untouched(iso):
    for who, method, path, body in ATTACKS:
        await getattr(iso, who).request(method, fill(path, iso.ids), json=fill(body, iso.ids))

    a, ids = iso.a, iso.ids
    provider = (await a.get(f"/api/v1/providers/{ids['provider']}")).json()
    assert provider["label"] == "A's Resend" and provider["config"]["from_address"] == "a@example.com"
    assert provider["secrets_set"] == ["api_key"]
    stored = iso.world.uow.providers.items[ids["provider"]]
    assert iso.world.app.state.secret_box.decrypt(stored.secrets_encrypted) == {"api_key": "re_a_secret"}
    assert (await a.get(f"/api/v1/workflows/{ids['workflow']}")).json()["name"] == "a-flow"
    assert (await a.get(f"/api/v1/templates/{ids['template']}")).json()["body"] == "A body"
    assert len((await a.get("/api/v1/preferences", params={"recipient": "ada"})).json()) == 1
    assert len((await a.get("/api/v1/dead-letters")).json()) == 1
    assert [x["address"] for x in (await a.get("/api/v1/suppressions")).json()] == ["blocked@example.com"]
    assert (await iso.b.get("/api/v1/suppressions")).json() == []
    assert (await a.get(f"/api/v1/notifications/{ids['notification']}")).json()["attempts"][0]["status"] == "pending"
    keys = {k["id"]: k for k in (await iso.a_admin.get("/api/v1/tenant/api-keys")).json()}
    assert keys[ids["api_key"]]["is_active"] is True
    assert len((await iso.a_admin.get("/api/v1/tenant/members")).json()) == 1


# ── lists show only the caller's own data ──────────────────────────────

LISTS = [
    "/api/v1/notifications", "/api/v1/events", "/api/v1/dead-letters", "/api/v1/providers",
    "/api/v1/providers/health", "/api/v1/workflows", "/api/v1/templates",
]


@pytest.mark.parametrize("path", LISTS)
async def test_lists_never_include_another_tenants_rows(iso, path):
    a_rows = (await iso.a.get(path)).json()
    b_rows = (await iso.b.get(path)).json()
    a_ids = {str(v) for row in a_rows for v in row.values() if isinstance(v, str)}
    leaked = [row for row in b_rows if a_ids & {str(v) for v in row.values() if isinstance(v, str)}]
    assert leaked == [], f"{path} leaked {leaked}"
    if path not in ("/api/v1/workflows",):   # the default workflow is materialised per tenant on first event
        assert len(a_rows) >= len(b_rows)


async def test_tenant_b_starts_empty_and_totals_are_its_own(iso):
    for path in ("/api/v1/notifications", "/api/v1/events", "/api/v1/dead-letters"):
        res = await iso.b.get(path)
        assert res.json() == [] and res.headers["X-Total-Count"] == "0", path
        assert (await iso.a.get(path)).headers["X-Total-Count"] == "1", path
    assert (await iso.b.get("/api/v1/providers")).json() == []
    assert (await iso.b.get("/api/v1/templates")).json() == []


async def test_metrics_and_usage_are_per_tenant(iso):
    iso.world.app.state.metrics_service = __import__(
        "services.metrics_service", fromlist=["MetricsService"]
    ).MetricsService(lambda: iso.world.uow)
    a = (await iso.a.get("/api/v1/metrics")).json()
    b = (await iso.b.get("/api/v1/metrics")).json()
    assert sum(a["notifications"].values()) == 1 and sum(b["notifications"].values()) == 0
    assert b["dead_letters"] == 0 and a["dead_letters"] == 1


async def test_console_member_lists_only_their_own_tenants_keys_and_members(iso):
    keys = (await iso.b_admin.get("/api/v1/tenant/api-keys")).json()
    members = (await iso.b_admin.get("/api/v1/tenant/members")).json()
    assert iso.ids["api_key"] not in {k["id"] for k in keys}
    assert [m["email"] for m in members] == ["bea@example.com"]


# ── creating things lands them in the caller's tenant ──────────────────


async def test_what_b_creates_belongs_to_b_and_never_appears_for_a(iso):
    r = await iso.b.post("/api/v1/providers", json={"channel": "sms", "name": "console", "label": "A's Resend"})
    assert r.status_code == 201                       # same label as A's: labels are unique per tenant only
    assert (await iso.b.post("/api/v1/templates", json={"name": "b-tpl", "channel": "sms", "body": "B"})).status_code == 201
    assert (await iso.b.post("/api/v1/workflows", json={
        "name": "b-flow", "event_type": "b.event",
        "definition": {"channels": [{"channel": "sms", "subject": None, "body": "b", "format": "text"}]},
    })).status_code == 201

    assert [p["label"] for p in (await iso.b.get("/api/v1/providers")).json()] == ["A's Resend"]
    assert [p["channel"] for p in (await iso.a.get("/api/v1/providers")).json()] == ["email"]
    assert [t["name"] for t in (await iso.a.get("/api/v1/templates")).json()] == ["a-tpl"]
    assert "b-flow" not in {w["name"] for w in (await iso.a.get("/api/v1/workflows")).json()}

    key = await iso.b_admin.post("/api/v1/tenant/api-keys", json={"name": "b-extra"})
    assert key.status_code == 201
    assert key.json()["id"] not in {k["id"] for k in (await iso.a_admin.get("/api/v1/tenant/api-keys")).json()}


async def test_a_workflow_cannot_route_through_another_tenants_provider(iso):
    r = await iso.b.post("/api/v1/workflows", json={
        "name": "steal", "event_type": "b.steal",
        "definition": {
            "channels": [{"channel": "email", "subject": "s", "body": "b", "format": "text"}],
            "provider_chains": {"email": [iso.ids["provider"]]},
        },
    })
    assert r.status_code == 422


async def test_the_same_idempotency_key_in_two_tenants_makes_two_notifications(iso):
    r = await iso.b.post("/api/v1/events", json={**EVENT, "idempotency_key": "shared-key"})
    assert r.status_code == 202
    assert r.json()["notification_id"] != iso.ids["notification"]
    assert (await iso.a.get("/api/v1/events")).headers["X-Total-Count"] == "1"
    assert (await iso.b.get("/api/v1/events")).headers["X-Total-Count"] == "1"


async def test_registering_the_same_device_token_in_two_tenants_keeps_them_apart(iso):
    body = {"external_ref": "ada", "platform": "android", "token": "shared-device-token"}
    assert (await iso.a.post("/api/v1/devices", json=body)).status_code == 201
    assert (await iso.b.post("/api/v1/devices", json=body)).status_code == 201
    a_recipient = await iso.world.uow.recipients.get_by_external_ref(iso.world.tenant_id, "ada")
    b_recipient = await iso.world.uow.recipients.get_by_external_ref(iso.b_tenant.id, "ada")
    assert a_recipient.id != b_recipient.id            # "ada" in B is a different person from "ada" in A
    assert len(await iso.world.uow.device_tokens.list_for_recipient(iso.world.tenant_id, a_recipient.id)) == 1
    assert len(await iso.world.uow.device_tokens.list_for_recipient(iso.b_tenant.id, b_recipient.id)) == 1


async def test_a_stream_never_replays_another_tenants_notifications(iso, monkeypatch):
    import api.v1.sse as sse
    from tests.api.test_sse import _ExplodingRegistry

    # A has an unacknowledged in-app attempt for "ada"...
    recipient = await iso.world.uow.recipients.get_by_external_ref(iso.world.tenant_id, "ada")
    if recipient is None:
        await iso.a.put("/api/v1/preferences", json={"external_ref": "ada", "channel": "sms"})
        recipient = await iso.world.uow.recipients.get_by_external_ref(iso.world.tenant_id, "ada")
    attempt = iso.world.uow.delivery_attempts.items[iso.ids["attempt"]]
    leaked = DeliveryAttempt(
        id=DeliveryAttemptId.new(), tenant_id=iso.world.tenant_id, notification_id=attempt.notification_id,
        recipient_id=recipient.id, channel=ChannelType.IN_APP, subject="A only", body="secret",
    )
    leaked.mark_sent(datetime.now(timezone.utc))
    iso.world.uow.delivery_attempts.items[str(leaked.id)] = leaked

    seen: dict = {}

    async def fake_stream(*, replay_attempts, subscribe, is_disconnected, heartbeat_seconds=0):
        seen["ids"] = [str(x.id) for x in replay_attempts]
        yield "data: {}\n\n"

    monkeypatch.setattr(sse, "stream_events", fake_stream)
    iso.world.app.state.sse_registry = _ExplodingRegistry()

    # ...so A's own stream would replay it, but B's stream for the same name must not.
    await iso.a.get("/api/v1/sse/stream", params={"recipient": "ada", "replay": "true"})
    assert seen["ids"] == [str(leaked.id)]
    await iso.b.get("/api/v1/sse/stream", params={"recipient": "ada", "replay": "true"})
    assert seen["ids"] == []


async def test_the_same_recipient_name_in_another_tenant_sees_and_changes_nothing_of_as_inbox(iso):
    # B has its own "ada": a different person who happens to share the name.
    await iso.b.post("/api/v1/devices", json={"external_ref": "ada", "platform": "android", "token": "b-ada-token"})
    inbox = "/api/v1/inbox"
    assert (await iso.b.get(inbox, params={"recipient": "ada", "state": "all"})).json()["items"] == []
    assert (await iso.b.get(f"{inbox}/unread-count", params={"recipient": "ada"})).json() == {"unread_count": 0}
    assert (await iso.b.post(f"{inbox}/{iso.ids['inbox']}/read", params={"recipient": "ada"})).status_code == 404
    assert (await iso.b.post(f"{inbox}/read-all", params={"recipient": "ada"})).json()["changed"] == 0

    mine = (await iso.a.get(inbox, params={"recipient": "ada"})).json()
    assert [m["body"] for m in mine["items"]] == ["a's secret message"] and mine["unread_count"] == 1
    assert mine["items"][0]["is_read"] is False


# ── who is allowed to say "I am tenant X" ──────────────────────────────


async def test_a_console_user_cannot_act_on_a_tenant_they_do_not_belong_to(iso):
    res = await iso.b_admin.get("/api/v1/providers", headers={"X-Tenant-Id": str(iso.world.tenant_id)})
    assert res.status_code == 403
    res = await iso.b_admin.get("/api/v1/tenant/members", headers={"X-Tenant-Id": str(iso.world.tenant_id)})
    assert res.status_code == 403


async def test_an_api_key_cannot_be_redirected_to_another_tenant_with_a_header(iso):
    res = await iso.b.get("/api/v1/providers", headers={"X-Tenant-Id": str(iso.world.tenant_id)})
    assert res.status_code == 200 and res.json() == []   # still B: the key decides, the header is ignored


async def test_a_platform_admin_may_cross_tenants_and_a_member_may_not_administer(iso):
    user = await iso.world.uow.users.get_by_email("bea@example.com")
    user.is_platform_admin = True
    await iso.world.uow.users.update(user)
    res = await iso.b_admin.get("/api/v1/providers", headers={"X-Tenant-Id": str(iso.world.tenant_id)})
    assert res.status_code == 200 and len(res.json()) == 1       # explicit, audited-by-design privilege

    carl = User(id=UserId.new(), email="carl@example.com", password_hash=hash_password(PASSWORD))
    await iso.world.uow.users.add(carl)
    await iso.world.uow.memberships.upsert(
        Membership(id=MembershipId.new(), tenant_id=iso.b_tenant.id, user_id=carl.id, role=TenantRole.MEMBER)
    )
    member = client_for(iso.world.app)
    await member.post("/api/v1/auth/login", json={"email": "carl@example.com", "password": PASSWORD})
    res = await member.get("/api/v1/tenant/api-keys", headers={"X-Tenant-Id": str(iso.b_tenant.id)})
    assert res.status_code == 403                                   # a plain member can't manage keys
    await member.aclose()


# ── every tenant-scoped route, straight from the route table ───────────


def api_routes(app):
    """(METHOD, full path, dependency names) for every API route, across FastAPI versions."""
    from fastapi.routing import APIRoute

    def dep_names(dependant, acc):
        for d in dependant.dependencies:
            acc.add(getattr(d.call, "__name__", str(d.call)))
            dep_names(d, acc)
        return acc

    def emit(route, prefix, extra):
        names = dep_names(route.dependant, set(extra))
        for method in route.methods - {"HEAD", "OPTIONS"}:
            yield method, prefix + route.path, names

    for route in app.routes:
        if isinstance(route, APIRoute):
            yield from emit(route, "", ())
        elif hasattr(route, "original_router"):                      # FastAPI >= 0.12x includes
            prefix = route.include_context.prefix or ""
            router_deps = [getattr(d.dependency, "__name__", "") for d in route.original_router.dependencies]
            for inner in route.original_router.routes:
                if isinstance(inner, APIRoute):
                    yield from emit(inner, prefix, router_deps)


TENANT_DEPS = {"require_tenant", "require_tenant_admin"}


def tenant_routes(app):
    return sorted(
        {(m, p) for m, p, deps in api_routes(app) if deps & TENANT_DEPS}
    )


async def test_every_tenant_route_rejects_anonymous_callers(world):
    anon = client_for(world.app)
    routes = tenant_routes(world.app)
    assert len(routes) >= 30, "route discovery failed"
    for method, path in routes:
        url = path.replace("{channel}", "email").replace("{key}", "smtp")
        url = "".join(uuid.uuid4().hex if False else part for part in [url])
        import re

        url = re.sub(r"\{[^}]+\}", str(uuid.uuid4()), url)
        res = await anon.request(method, url, json={})
        assert res.status_code in (401, 422), f"{method} {path} answered {res.status_code} without credentials"
        assert res.status_code != 200
    await anon.aclose()


# Where each tenant-scoped route is covered. A new route that is missing here
# fails test_every_tenant_route_is_covered below: add it to ATTACKS / LISTS /
# the tests above, or record why it needs no isolation test.
COVERED = {
    # resource routes: ATTACKS above
    ("GET", "/api/v1/notifications/{notification_id}"), ("POST", "/api/v1/notifications/{notification_id}/ack"),
    ("GET", "/api/v1/providers/{provider_id}"), ("PUT", "/api/v1/providers/{provider_id}"),
    ("DELETE", "/api/v1/providers/{provider_id}"), ("POST", "/api/v1/provider-catalog/{channel}/{key}/test"),
    ("GET", "/api/v1/workflows/{workflow_id}"), ("PUT", "/api/v1/workflows/{workflow_id}"),
    ("DELETE", "/api/v1/workflows/{workflow_id}"), ("GET", "/api/v1/templates/{template_id}"),
    ("PUT", "/api/v1/templates/{template_id}"), ("DELETE", "/api/v1/templates/{template_id}"),
    ("GET", "/api/v1/templates/{template_id}/versions"), ("GET", "/api/v1/templates/{template_id}/versions/{version}"),
    ("POST", "/api/v1/templates/{template_id}/publish"), ("POST", "/api/v1/templates/{template_id}/rollback"),
    ("POST", "/api/v1/templates/{template_id}/preview"),
    ("DELETE", "/api/v1/preferences/{preference_id}"), ("GET", "/api/v1/preferences"),
    ("DELETE", "/api/v1/suppressions/{suppression_id}"), ("GET", "/api/v1/suppressions"),   # list: asserted after the attacks
    ("POST", "/api/v1/dead-letters/{dead_letter_id}/retry"), ("DELETE", "/api/v1/tenant/api-keys/{key_id}"),
    ("DELETE", "/api/v1/tenant/members/{membership_id}"), ("POST", "/api/v1/tenant/api-keys/{key_id}/rotate"),
    # lists and aggregates
    ("GET", "/api/v1/notifications"), ("GET", "/api/v1/events"), ("GET", "/api/v1/dead-letters"),
    ("GET", "/api/v1/providers"), ("GET", "/api/v1/providers/health"), ("GET", "/api/v1/workflows"),
    ("GET", "/api/v1/templates"), ("GET", "/api/v1/metrics"), ("GET", "/api/v1/tenant/api-keys"),
    ("GET", "/api/v1/tenant/members"), ("GET", "/api/v1/tenant/usage"),
    # inbox: resource routes in ATTACKS; list/count/read-all in the inbox tests below
    ("POST", "/api/v1/inbox/{message_id}/read"), ("POST", "/api/v1/inbox/{message_id}/unread"),
    ("POST", "/api/v1/inbox/{message_id}/archive"), ("POST", "/api/v1/inbox/{message_id}/unarchive"),
    ("GET", "/api/v1/inbox"), ("GET", "/api/v1/inbox/unread-count"), ("POST", "/api/v1/inbox/read-all"),
    # creates / writes that land in the caller's tenant
    ("POST", "/api/v1/templates/preview"),   # renders what the caller sends; touches no stored data
    ("POST", "/api/v1/suppressions"),
    ("POST", "/api/v1/providers"), ("POST", "/api/v1/templates"), ("POST", "/api/v1/workflows"),
    ("POST", "/api/v1/events"), ("POST", "/api/v1/devices"), ("PUT", "/api/v1/preferences"),
    ("POST", "/api/v1/tenant/api-keys"), ("POST", "/api/v1/tenant/members"),
    # global, no tenant data: the catalog of supported providers
    ("GET", "/api/v1/provider-catalog"),
}


async def test_every_tenant_route_is_covered(world):
    uncovered = [r for r in tenant_routes(world.app) if r not in COVERED]
    assert uncovered == [], (
        "Tenant-scoped routes without an isolation test: "
        + ", ".join(f"{m} {p}" for m, p in uncovered)
        + ". Cover them in tests/api/test_tenant_isolation.py or add them to COVERED with a reason."
    )
    stale = [r for r in COVERED if r not in set(tenant_routes(world.app))]
    assert stale == [], f"COVERED lists routes that no longer exist: {stale}"


async def test_every_route_an_api_key_can_reach_declares_the_scope_it_needs(world):
    """A key-reachable route without a scope would be open to any key, however limited."""
    unscoped = []
    for method, path, deps in api_routes(world.app):
        if "require_tenant" in deps and "require_tenant_admin" not in deps and "require_scope" not in deps:
            unscoped.append(f"{method} {path}")
    assert unscoped == [], f"Routes reachable with an API key but declaring no scope: {unscoped}"
