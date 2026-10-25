from __future__ import annotations

import pytest

from api.audit import classify

WORKFLOW = {
    "name": "w", "event_type": "w.event",
    "definition": {"channels": [{"channel": "email", "subject": "s", "body": "b", "format": "text"}]},
}


@pytest.mark.parametrize("method, path, expected", [
    ("POST", "/api/v1/workflows", ("workflow", "workflow.create")),
    ("PUT", "/api/v1/workflows/{workflow_id}", ("workflow", "workflow.update")),
    ("DELETE", "/api/v1/templates/{template_id}", ("template", "template.delete")),
    ("POST", "/api/v1/templates/{template_id}/publish", ("template", "template.publish")),
    ("POST", "/api/v1/templates/{template_id}/rollback", ("template", "template.rollback")),
    ("POST", "/api/v1/tenant/api-keys/{key_id}/rotate", ("api_key", "api_key.rotate")),
    ("DELETE", "/api/v1/tenant/members/{membership_id}", ("member", "member.delete")),
    ("POST", "/api/v1/templates/preview", None),
    ("POST", "/api/v1/templates/{template_id}/preview", None),
    ("POST", "/api/v1/provider-catalog/{channel}/{key}/test", None),
    ("POST", "/api/v1/events", None),
    ("GET", "/api/v1/workflows", None),
])
def test_classify(method, path, expected):
    assert classify(method, path) == expected


async def test_changes_are_recorded_with_actor_and_resource_but_no_bodies(world):
    h = world.key_headers
    wid = (await world.client.post("/api/v1/workflows", json=WORKFLOW, headers=h)).json()["id"]
    r = await world.client.put(f"/api/v1/workflows/{wid}", json={**WORKFLOW, "name": "renamed"}, headers=h)
    assert r.status_code == 200
    assert (await world.client.delete(f"/api/v1/workflows/{wid}", headers=h)).status_code == 204
    await world.client.get("/api/v1/workflows", headers=h)                        # reads are not audited
    await world.client.post("/api/v1/workflows", json={"name": "bad"}, headers=h)  # failures are not audited

    log = (await world.client.get("/api/v1/audit-log", headers=h)).json()
    assert [e["action"] for e in log] == ["workflow.delete", "workflow.update", "workflow.create"]
    assert {e["resource_id"] for e in log} == {wid}
    assert log[0]["actor_type"] == "api_key" and log[0]["actor_id"]
    assert "renamed" not in str(log)

    only = (await world.client.get("/api/v1/audit-log", params={"resource_type": "template"}, headers=h)).json()
    assert only == []


async def test_console_user_actions_show_the_email(world):
    from tests.api.conftest import PASSWORD

    await world.client.post("/api/v1/auth/login", json={"email": "ada@example.com", "password": PASSWORD})
    headers = {"X-Tenant-Id": str(world.tenant_id)}
    r = await world.client.post("/api/v1/templates", json={"name": "t", "channel": "email", "body": "b"}, headers=headers)
    assert r.status_code == 201, r.text
    [entry] = world.uow.audit.items
    assert (entry.actor_type, entry.actor_label, entry.action) == ("user", "ada@example.com", "template.create")
    assert entry.resource_id == r.json()["id"]
