"""C3 on the real API and Postgres: tenant B attacks tenant A's data with an API key."""
from __future__ import annotations

import httpx
import pytest

from tests.api.test_tenant_isolation import ATTACKS, fill
from tests.e2e.harness import Stack


@pytest.fixture(scope="module")
def stack():
    s = Stack("isolation", redis_db=10).start()
    try:
        yield s
    except BaseException:
        print(s.dump_logs())
        raise
    finally:
        s.stop()


@pytest.fixture(scope="module")
def tenants(stack):
    """Tenant A (the stack's own) owns data; tenant B is a stranger with a valid key."""
    b_key, b_id = stack.add_tenant("bravo")
    b = httpx.Client(base_url=f"http://127.0.0.1:{stack.port}/api/v1", headers={"X-API-Key": b_key}, timeout=15)

    ids = {}
    ids["provider"] = stack.api("POST", "/providers", json={
        "channel": "email", "name": "resend", "label": "A's Resend",
        "config": {"from_address": "a@example.com"}, "secrets": {"api_key": "re_a_secret"},
    }).json()["id"]
    ids["workflow"] = stack.api("POST", "/workflows", json={
        "name": "a-flow", "event_type": "a.event",
        "definition": {"channels": [{"channel": "email", "subject": "s", "body": "b", "format": "text"}]},
    }).json()["id"]
    ids["template"] = stack.api("POST", "/templates", json={"name": "a-tpl", "channel": "email", "body": "A"}).json()["id"]
    ids["preference"] = stack.api("PUT", "/preferences", json={"external_ref": "ada", "channel": "sms", "opted_in": False}).json()["id"]
    ev = stack.api("POST", "/events", json={
        "event_type": "a.event", "source": "e2e", "idempotency_key": "k",
        "payload": {"recipients": [{"external_ref": "ada", "email": "ada@example.com"}]},
    })
    ids["notification"] = ev.json()["notification_id"]
    ids["attempt"] = stack.notification(ids["notification"])["attempts"][0]["id"]
    [row] = stack.sql(
        """INSERT INTO inbox_messages (id, tenant_id, recipient_id, notification_id, attempt_id, title, body)
           SELECT gen_random_uuid(), tenant_id, recipient_id, notification_id, id, 'A only', 'a secret'
           FROM delivery_attempts WHERE id = $1::uuid RETURNING id""",
        ids["attempt"],
    )
    ids["inbox"] = str(row["id"])
    yield b, ids
    b.close()


ATTACKS_BY_KEY = [a for a in ATTACKS if a[0] == "b" and "{dead_letter}" not in a[2]]


@pytest.mark.parametrize("who, method, path, body", ATTACKS_BY_KEY, ids=[f"{m} {p}" for _, m, p, _ in ATTACKS_BY_KEY])
def test_another_tenants_resource_is_a_404_on_real_postgres(stack, tenants, who, method, path, body):
    b, ids = tenants
    res = b.request(method, fill(path, ids).replace("/api/v1", ""), json=fill(body, ids))
    assert res.status_code == 404, f"{method} {path} -> {res.status_code}: {res.text}"
    assert "re_a_secret" not in res.text


def test_lists_are_empty_for_the_stranger_and_a_is_untouched(stack, tenants):
    b, ids = tenants
    for path in ("/notifications", "/events", "/dead-letters", "/providers", "/templates"):
        assert b.get(path).json() == [], path
    assert [w["name"] for w in b.get("/workflows").json()] == []

    provider = stack.api("GET", f"/providers/{ids['provider']}").json()
    assert provider["label"] == "A's Resend" and provider["secrets_set"] == ["api_key"]
    assert stack.api("GET", f"/workflows/{ids['workflow']}").json()["name"] == "a-flow"
    assert stack.api("GET", f"/templates/{ids['template']}").json()["body"] == "A"
    assert stack.notification(ids["notification"])["attempts"][0]["id"] == ids["attempt"]
    assert len(stack.api("GET", "/preferences", params={"recipient": "ada"}).json()) == 1
