"""C4: API key scopes, expiry, last use and rotation."""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from domain.value_objects.scopes import ALL_SCOPES, satisfies
from services.identity_service import IdentityService
from tests.api.conftest import PASSWORD
from tests.api.test_tenant_isolation import client_for

EVENT = {"event_type": "user.signup", "source": "quotient", "payload": {"plan": "pro"}}


@pytest.fixture
async def admin(world):
    """A console session for the tenant admin (the only way to manage keys)."""
    c = client_for(world.app)
    assert (await c.post("/api/v1/auth/login", json={"email": "ada@example.com", "password": PASSWORD})).status_code == 200
    c.headers["X-Tenant-Id"] = str(world.tenant_id)
    yield c
    await c.aclose()


async def new_key(admin, *scopes, **extra):
    r = await admin.post("/api/v1/tenant/api-keys", json={"name": "k-" + "-".join(scopes or ["default"]), **({"scopes": list(scopes)} if scopes else {}), **extra})
    assert r.status_code == 201, r.text
    return r.json()


def iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))   # 3.10 cannot parse a trailing Z


def using(world, key) -> httpx.AsyncClient:
    return client_for(world.app, {"X-API-Key": key["key"]})


# ── what each scope allows, derived from the real route table ──────────


def scoped_routes(app):
    """(METHOD, path, accepted scopes) for every route an API key can reach."""
    from fastapi.routing import APIRoute

    def accepted(dependant):
        for d in dependant.dependencies:
            found = getattr(d.call, "scopes", None)
            if found:
                return found
            inner = accepted(d)
            if inner:
                return inner
        return None

    out = []
    for route in app.routes:
        if hasattr(route, "original_router"):
            prefix = route.include_context.prefix or ""
            for inner in route.original_router.routes:
                if isinstance(inner, APIRoute) and (scopes := accepted(inner.dependant)):
                    for m in inner.methods - {"HEAD", "OPTIONS"}:
                        out.append((m, prefix + inner.path, scopes))
    return out


def concrete(path):
    path = path.replace("{channel}", "email").replace("{key}", "smtp")
    return re.sub(r"\{[^}]+\}", str(uuid.uuid4()), path)


async def test_every_scoped_route_is_closed_to_keys_without_its_scope_and_open_to_those_with_it(world, admin):
    routes = scoped_routes(world.app)
    assert len(routes) >= 30, "route discovery failed"
    keys = {s: using(world, await new_key(admin, s)) for s in ALL_SCOPES}
    try:
        for method, path, accepted in routes:
            for scope, client in keys.items():
                res = await client.request(method, concrete(path), json={})
                if satisfies({scope}, accepted):
                    assert res.status_code not in (401, 403), f"{scope} key refused on {method} {path}: {res.text}"
                else:
                    assert res.status_code == 403, f"{scope} key reached {method} {path} -> {res.status_code}"
                    assert "scope" in res.json()["detail"]
    finally:
        for c in keys.values():
            await c.aclose()


async def test_admin_implies_every_other_scope():
    assert satisfies({"admin"}, ["ingest"]) and satisfies({"admin"}, ["client", "read"])
    assert not satisfies({"read"}, ["ingest"]) and not satisfies(set(), ["read"])
    assert satisfies({"client"}, ["read", "client"])          # any-of


async def test_the_scopes_of_the_main_flows(world, admin):
    producer = using(world, await new_key(admin, "ingest"))
    reader = using(world, await new_key(admin, "read"))
    app_key = using(world, await new_key(admin, "client"))
    try:
        sent = await producer.post("/api/v1/events", json={**EVENT, "idempotency_key": "s1"})
        assert sent.status_code == 202
        nid = sent.json()["notification_id"]
        assert (await producer.get(f"/api/v1/notifications/{nid}")).status_code == 403   # a producer can't read back

        assert (await reader.get(f"/api/v1/notifications/{nid}")).status_code == 200
        assert (await reader.post("/api/v1/events", json={**EVENT, "idempotency_key": "s2"})).status_code == 403
        assert (await reader.post("/api/v1/providers", json={"channel": "sms", "name": "console"})).status_code == 403

        attempt = (await reader.get(f"/api/v1/notifications/{nid}")).json()["attempts"][0]["id"]
        assert (await app_key.post("/api/v1/devices", json={"external_ref": "u1", "platform": "ios", "token": "t"})).status_code == 201
        assert (await app_key.put("/api/v1/preferences", json={"external_ref": "u1", "channel": "sms"})).status_code == 200
        assert (await app_key.get("/api/v1/preferences", params={"recipient": "u1"})).status_code == 200   # read OR client
        assert (await app_key.get("/api/v1/notifications")).status_code == 403
        assert (await app_key.post("/api/v1/events", json={**EVENT, "idempotency_key": "s3"})).status_code == 403
        ack = await app_key.post(f"/api/v1/notifications/{nid}/ack", json={"attempt_id": attempt})
        assert ack.status_code != 403
    finally:
        for c in (producer, reader, app_key):
            await c.aclose()


async def test_an_admin_key_can_manage_configuration_but_not_keys_or_members(world, admin):
    key = using(world, await new_key(admin, "admin"))
    try:
        r = await key.post("/api/v1/providers", json={"channel": "sms", "name": "console", "label": "From a key"})
        assert r.status_code == 201
        assert (await key.get("/api/v1/tenant/api-keys")).status_code in (401, 403)     # key management is session-only
    finally:
        await key.aclose()


async def test_console_sessions_are_not_subject_to_key_scopes(admin):
    assert (await admin.get("/api/v1/notifications")).status_code == 200
    assert (await admin.post("/api/v1/providers", json={"channel": "sms", "name": "console"})).status_code == 201


async def test_the_sse_stream_needs_the_client_scope(world, admin):
    from tests.api.test_sse import _ExplodingRegistry, _StopStream

    world.app.state.sse_registry = _ExplodingRegistry()
    reader = using(world, await new_key(admin, "read"))
    app_key = using(world, await new_key(admin, "client"))
    try:
        denied = await reader.get("/api/v1/sse/stream", params={"recipient": "u1"})
        assert denied.status_code == 403 and "client" in denied.json()["detail"]
        with pytest.raises(_StopStream):                       # got past auth into the (stubbed) stream
            await app_key.get("/api/v1/sse/stream", params={"recipient": "u1"})
    finally:
        await reader.aclose()
        await app_key.aclose()


# ── creating keys ──────────────────────────────────────────────────────


async def test_new_keys_default_to_ingest_only(admin):
    r = await admin.post("/api/v1/tenant/api-keys", json={"name": "producer"})
    assert r.status_code == 201 and r.json()["scopes"] == ["ingest"]


async def test_scope_validation(admin):
    assert (await admin.post("/api/v1/tenant/api-keys", json={"name": "x", "scopes": ["superuser"]})).status_code == 422
    assert (await admin.post("/api/v1/tenant/api-keys", json={"name": "x", "scopes": []})).status_code == 422
    dup = await admin.post("/api/v1/tenant/api-keys", json={"name": "x", "scopes": ["read", "read"]})
    assert dup.status_code == 201 and dup.json()["scopes"] == ["read"]


async def test_listing_shows_scopes_and_status_but_never_the_secret(admin):
    created = await new_key(admin, "read")
    listed = (await admin.get("/api/v1/tenant/api-keys")).json()
    row = next(k for k in listed if k["id"] == created["id"])
    assert row["scopes"] == ["read"] and row["status"] == "active" and row["key"] is None
    assert row["prefix"] == created["key"][:12]
    assert created["key"] not in str(listed)


# ── expiry ─────────────────────────────────────────────────────────────


async def test_a_key_stops_working_when_it_expires(world, admin):
    key = await new_key(admin, "read", expires_in_days=30)
    assert key["expires_at"] is not None and key["status"] == "active"
    client = using(world, key)
    assert (await client.get("/api/v1/events")).status_code == 200

    row = world.uow.api_keys.items[key["id"]]
    row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    assert (await client.get("/api/v1/events")).status_code == 401
    listed = {k["id"]: k for k in (await admin.get("/api/v1/tenant/api-keys")).json()}
    assert listed[key["id"]]["status"] == "expired"
    await client.aclose()


async def test_a_revoked_key_is_refused_and_reported(world, admin):
    key = await new_key(admin, "read")
    assert (await admin.delete(f"/api/v1/tenant/api-keys/{key['id']}")).status_code == 204
    async with using(world, key) as client:
        assert (await client.get("/api/v1/events")).status_code == 401
    listed = {k["id"]: k for k in (await admin.get("/api/v1/tenant/api-keys")).json()}
    assert listed[key["id"]]["status"] == "revoked"


async def test_keys_without_an_expiry_never_expire(world, admin):
    key = await new_key(admin, "read")
    assert key["expires_at"] is None
    async with using(world, key) as client:
        assert (await client.get("/api/v1/events")).status_code == 200


# ── last used ──────────────────────────────────────────────────────────


async def test_last_use_is_recorded_but_not_on_every_request(world, admin):
    key = await new_key(admin, "read")
    row = world.uow.api_keys.items[key["id"]]
    assert row.last_used_at is None
    async with using(world, key) as client:
        await client.get("/api/v1/events")
        first = row.last_used_at
        assert first is not None
        row.last_used_at = None                                # pretend it is cleared...
        await client.get("/api/v1/events")                     # ...a request within the minute doesn't rewrite it
        assert row.last_used_at is None
        world.app.state.identity_service._last_touched.clear()   # a minute later
        await client.get("/api/v1/events")
        assert row.last_used_at is not None
    listed = {k["id"]: k for k in (await admin.get("/api/v1/tenant/api-keys")).json()}
    assert listed[key["id"]]["last_used_at"] is not None


# ── rotation ───────────────────────────────────────────────────────────


async def test_rotation_issues_a_replacement_and_keeps_the_old_key_for_the_grace_period(world, admin):
    old = await new_key(admin, "read", "ingest", expires_in_days=90)
    r = await admin.post(f"/api/v1/tenant/api-keys/{old['id']}/rotate", json={"grace_hours": 6})
    assert r.status_code == 201, r.text
    new = r.json()
    assert new["id"] != old["id"] and new["key"] and new["key"] != old["key"]
    assert new["scopes"] == ["read", "ingest"] and new["name"] == old["name"]
    assert new["rotated_from"] == old["id"] and new["expires_at"] == old["expires_at"]   # same lifetime and rights

    valid_until = iso(new["previous_key_valid_until"])
    assert timedelta(hours=5, minutes=59) < valid_until - datetime.now(timezone.utc) <= timedelta(hours=6)

    async with using(world, old) as old_client, using(world, new) as new_client:
        assert (await old_client.get("/api/v1/events")).status_code == 200     # clients can migrate calmly
        assert (await new_client.get("/api/v1/events")).status_code == 200

    listed = {k["id"]: k for k in (await admin.get("/api/v1/tenant/api-keys")).json()}
    assert listed[old["id"]]["replaced_by"] == new["id"] and listed[new["id"]]["rotated_from"] == old["id"]
    assert listed[old["id"]]["status"] == "active"                             # until the grace ends


async def test_the_old_key_stops_when_the_grace_period_ends(world, admin):
    old = await new_key(admin, "read")
    await admin.post(f"/api/v1/tenant/api-keys/{old['id']}/rotate", json={"grace_hours": 1})
    world.uow.api_keys.items[old["id"]].expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    async with using(world, old) as client:
        assert (await client.get("/api/v1/events")).status_code == 401
    assert {k["id"]: k for k in (await admin.get("/api/v1/tenant/api-keys")).json()}[old["id"]]["status"] == "expired"


async def test_zero_grace_revokes_the_old_key_immediately(world, admin):
    old = await new_key(admin, "read")
    r = await admin.post(f"/api/v1/tenant/api-keys/{old['id']}/rotate", json={"grace_hours": 0})
    assert r.status_code == 201
    async with using(world, old) as old_client, using(world, r.json()) as new_client:
        assert (await old_client.get("/api/v1/events")).status_code == 401
        assert (await new_client.get("/api/v1/events")).status_code == 200
    assert {k["id"]: k for k in (await admin.get("/api/v1/tenant/api-keys")).json()}[old["id"]]["status"] == "revoked"


async def test_rotating_never_extends_a_keys_own_expiry(world, admin):
    old = await new_key(admin, "read", expires_in_days=1)
    r = await admin.post(f"/api/v1/tenant/api-keys/{old['id']}/rotate", json={"grace_hours": 24 * 7})
    valid_until = iso(r.json()["previous_key_valid_until"])
    assert valid_until <= iso(old["expires_at"])            # capped by the original expiry


async def test_rotation_defaults_to_a_day_of_grace_with_no_body(admin):
    old = await new_key(admin, "read")
    r = await admin.post(f"/api/v1/tenant/api-keys/{old['id']}/rotate")
    assert r.status_code == 201
    left = iso(r.json()["previous_key_valid_until"]) - datetime.now(timezone.utc)
    assert timedelta(hours=23, minutes=59) < left <= timedelta(hours=24)


async def test_only_an_active_key_can_be_rotated(admin):
    old = await new_key(admin, "read")
    await admin.delete(f"/api/v1/tenant/api-keys/{old['id']}")
    assert (await admin.post(f"/api/v1/tenant/api-keys/{old['id']}/rotate")).status_code == 409
    assert (await admin.post(f"/api/v1/tenant/api-keys/{uuid.uuid4()}/rotate")).status_code == 404


async def test_a_key_cannot_rotate_itself_or_anything_else(world, admin):
    key = using(world, await new_key(admin, "admin"))
    other = await new_key(admin, "read")
    try:
        r = await key.post(f"/api/v1/tenant/api-keys/{other['id']}/rotate")
        assert r.status_code in (401, 403)             # stolen key can't mint itself a successor
    finally:
        await key.aclose()


# ── existing keys keep working ─────────────────────────────────────────


async def test_the_bootstrap_key_and_pre_scope_keys_hold_every_scope(world):
    identity: IdentityService = world.app.state.identity_service
    token, key = await identity.create_api_key(world.tenant_id, "legacy")      # no scopes: the old behaviour
    assert set(key.scopes) == ALL_SCOPES
    async with client_for(world.app, {"X-API-Key": token}) as c:
        assert (await c.post("/api/v1/providers", json={"channel": "sms", "name": "console"})).status_code == 201
