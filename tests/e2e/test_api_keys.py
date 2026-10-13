"""C4 on the real API and Postgres: scoped keys, expiry, last use and rotation."""
from __future__ import annotations

import uuid

import httpx
import pytest

from tests.e2e.harness import SseClient, Stack, wait_for


@pytest.fixture(scope="module")
def stack():
    s = Stack("keys", redis_db=8).start()
    try:
        yield s
    except BaseException:
        print(s.dump_logs())
        raise
    finally:
        s.stop()


@pytest.fixture(scope="module")
def admin(stack):
    c = stack.admin_client()
    yield c
    c.close()


def make_key(admin, *scopes, **extra):
    r = admin.post("/tenant/api-keys", json={"name": "-".join(scopes), "scopes": list(scopes), **extra})
    assert r.status_code == 201, r.text
    return r.json()


def as_key(stack, key) -> httpx.Client:
    return httpx.Client(base_url=f"http://127.0.0.1:{stack.port}/api/v1", headers={"X-API-Key": key["key"]}, timeout=15)


def event(user="u1"):
    return {"event_type": "user.signup", "source": "e2e", "idempotency_key": str(uuid.uuid4()),
            "payload": {"recipients": [{"external_ref": user, "email": f"{user}@example.com"}]}}


def test_each_scope_opens_only_its_own_doors(stack, admin):
    producer, reader = make_key(admin, "ingest"), make_key(admin, "read")
    with as_key(stack, producer) as p, as_key(stack, reader) as r:
        sent = p.post("/events", json=event())
        assert sent.status_code == 202, sent.text
        nid = sent.json()["notification_id"]
        assert p.get(f"/notifications/{nid}").status_code == 403
        assert p.get("/providers").status_code == 403

        assert r.get(f"/notifications/{nid}").status_code == 200
        assert r.post("/events", json=event()).status_code == 403
        assert r.post("/providers", json={"channel": "sms", "name": "console"}).status_code == 403


def test_the_sse_stream_requires_the_client_scope(stack, admin):
    denied = SseClient(stack, "ada")           # the stack's own key holds every scope...
    denied.close()
    reader = make_key(admin, "read")
    r = httpx.get(f"http://127.0.0.1:{stack.port}/api/v1/sse/stream", params={"recipient": "ada"},
                  headers={"X-API-Key": reader["key"]}, timeout=5)
    assert r.status_code == 403 and "client" in r.json()["detail"]

    app_key = make_key(admin, "client")
    with httpx.stream("GET", f"http://127.0.0.1:{stack.port}/api/v1/sse/stream", params={"recipient": "ada"},
                      headers={"X-API-Key": app_key["key"]}, timeout=5) as ok:
        assert ok.status_code == 200


def test_last_use_is_recorded_in_postgres(stack, admin):
    key = make_key(admin, "read")
    [before] = stack.sql("SELECT last_used_at FROM api_keys WHERE id = $1::uuid", key["id"])
    assert before["last_used_at"] is None
    with as_key(stack, key) as c:
        assert c.get("/events").status_code == 200
    row = wait_for(lambda: stack.sql("SELECT last_used_at FROM api_keys WHERE id = $1::uuid", key["id"])[0]["last_used_at"],
                   what="last_used_at to be written")
    assert row is not None
    listed = {k["id"]: k for k in admin.get("/tenant/api-keys").json()}
    assert listed[key["id"]]["last_used_at"] is not None


def test_an_expired_key_is_refused_by_the_real_api(stack, admin):
    key = make_key(admin, "read", expires_in_days=7)
    with as_key(stack, key) as c:
        assert c.get("/events").status_code == 200
        stack.sql("UPDATE api_keys SET expires_at = now() - interval '1 second' WHERE id = $1::uuid", key["id"])
        assert c.get("/events").status_code == 401
    assert {k["id"]: k for k in admin.get("/tenant/api-keys").json()}[key["id"]]["status"] == "expired"


def test_rotation_with_a_grace_period_then_cutover(stack, admin):
    old = make_key(admin, "read", "ingest")
    r = admin.post(f"/tenant/api-keys/{old['id']}/rotate", json={"grace_hours": 2})
    assert r.status_code == 201, r.text
    new = r.json()
    assert new["scopes"] == ["read", "ingest"] and new["rotated_from"] == old["id"]

    with as_key(stack, old) as o, as_key(stack, new) as n:
        assert o.get("/events").status_code == 200 and n.get("/events").status_code == 200   # overlap: no downtime
        stack.sql("UPDATE api_keys SET expires_at = now() - interval '1 second' WHERE id = $1::uuid", old["id"])
        assert o.get("/events").status_code == 401                                          # grace over
        assert n.get("/events").status_code == 200

    listed = {k["id"]: k for k in admin.get("/tenant/api-keys").json()}
    assert listed[old["id"]]["replaced_by"] == new["id"] and listed[old["id"]]["status"] == "expired"


def test_immediate_rotation_revokes_the_old_key(stack, admin):
    old = make_key(admin, "read")
    new = admin.post(f"/tenant/api-keys/{old['id']}/rotate", json={"grace_hours": 0}).json()
    with as_key(stack, old) as o, as_key(stack, new) as n:
        assert o.get("/events").status_code == 401 and n.get("/events").status_code == 200
    assert admin.post(f"/tenant/api-keys/{old['id']}/rotate").status_code == 409     # already retired


def test_keys_are_still_isolated_between_tenants(stack, admin):
    other_key, _ = stack.add_tenant("bravo")
    mine = make_key(admin, "admin")
    with httpx.Client(base_url=f"http://127.0.0.1:{stack.port}/api/v1", headers={"X-API-Key": other_key}, timeout=15) as b:
        assert b.get("/tenant/api-keys").status_code in (401, 403)
        assert b.post(f"/tenant/api-keys/{mine['id']}/rotate").status_code in (401, 403)
