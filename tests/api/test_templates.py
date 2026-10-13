"""Template drafts, versions, rollback and preview through the API."""
from __future__ import annotations

import pytest

T = "/api/v1/templates"


def H(world):
    return world.key_headers


async def create(world, publish=False, **over):
    body = {"name": "welcome", "channel": "email", "subject": "Hi {{ recipient.external_ref }}",
            "body": "Welcome {{ recipient.external_ref }}, you joined {{ plan }}", "format": "text",
            "publish": publish, **over}
    r = await world.client.post(T, headers=H(world), json=body)
    assert r.status_code == 201, r.text
    return r.json()


def put_body(t, **over):
    keys = ("name", "channel", "subject", "body", "format", "locale", "is_active")
    return {**{k: t[k] for k in keys}, **over}


async def test_a_new_template_is_a_draft_until_published(world):
    t = await create(world)
    assert t["published_version"] is None and t["has_unpublished_changes"] is True
    assert (await world.client.get(f"{T}/{t['id']}/versions", headers=H(world))).json() == []


async def test_create_can_publish_straight_away(world):
    t = await create(world, publish=True)
    assert t["published_version"] == 1 and t["has_unpublished_changes"] is False
    [v] = (await world.client.get(f"{T}/{t['id']}/versions", headers=H(world))).json()
    assert v["version"] == 1 and v["is_published"] is True and v["note"] == "First version"


async def test_editing_changes_the_draft_and_publishing_makes_it_live(world):
    t = await create(world, publish=True)
    r = await world.client.put(f"{T}/{t['id']}", headers=H(world), json=put_body(t, body="New text"))
    assert r.status_code == 200
    edited = r.json()
    assert edited["body"] == "New text" and edited["published_version"] == 1 and edited["has_unpublished_changes"] is True
    assert (await world.client.get(f"{T}/{t['id']}/versions/1", headers=H(world))).json()["body"].startswith("Welcome")

    pub = await world.client.post(f"{T}/{t['id']}/publish", headers=H(world), json={"note": "tone"})
    assert pub.status_code == 200 and pub.json()["published_version"] == 2 and pub.json()["has_unpublished_changes"] is False
    versions = (await world.client.get(f"{T}/{t['id']}/versions", headers=H(world))).json()
    assert [(v["version"], v["is_published"], v["note"]) for v in versions] == [(2, True, "tone"), (1, False, "First version")]


async def test_publishing_with_nothing_new_is_a_409(world):
    t = await create(world, publish=True)
    r = await world.client.post(f"{T}/{t['id']}/publish", headers=H(world))
    assert r.status_code == 409 and "matches" in r.text


async def test_rollback_publishes_an_older_version_as_a_new_one(world):
    t = await create(world, publish=True)
    await world.client.put(f"{T}/{t['id']}", headers=H(world), json=put_body(t, body="Typo version"))
    await world.client.post(f"{T}/{t['id']}/publish", headers=H(world))

    r = await world.client.post(f"{T}/{t['id']}/rollback", headers=H(world), json={"version": 1})
    assert r.status_code == 200
    out = r.json()
    assert out["published_version"] == 3 and out["body"].startswith("Welcome") and out["has_unpublished_changes"] is False
    assert (await world.client.post(f"{T}/{t['id']}/rollback", headers=H(world), json={"version": 9})).status_code == 404
    assert (await world.client.post(f"{T}/{t['id']}/rollback", headers=H(world), json={"version": 0})).status_code == 422


async def test_unknown_versions_are_404(world):
    t = await create(world, publish=True)
    assert (await world.client.get(f"{T}/{t['id']}/versions/7", headers=H(world))).status_code == 404


async def test_the_list_flags_unpublished_changes(world):
    a = await create(world, publish=True, name="a")
    await create(world, publish=False, name="b")
    await world.client.put(f"{T}/{a['id']}", headers=H(world), json=put_body(a, body="edited"))
    rows = {r["name"]: r for r in (await world.client.get(T, headers=H(world))).json()}
    assert rows["a"]["has_unpublished_changes"] is True and rows["a"]["published_version"] == 1
    assert rows["b"]["published_version"] is None


# ── preview ────────────────────────────────────────────────────────────

SAMPLE = {"data": {"plan": "pro"}, "recipient": {"external_ref": "ada"}}


async def test_preview_renders_the_draft_by_default(world):
    t = await create(world, publish=True)
    await world.client.put(f"{T}/{t['id']}", headers=H(world), json=put_body(t, body="Draft says {{ plan }}"))
    r = await world.client.post(f"{T}/{t['id']}/preview", headers=H(world), json=SAMPLE)
    assert r.status_code == 200
    assert r.json() == {"subject": "Hi ada", "text": "Draft says pro", "html": None, "version": "draft"}


async def test_preview_can_render_the_published_or_any_version(world):
    t = await create(world, publish=True)
    await world.client.put(f"{T}/{t['id']}", headers=H(world), json=put_body(t, body="Second {{ plan }}"))
    await world.client.post(f"{T}/{t['id']}/publish", headers=H(world))
    await world.client.put(f"{T}/{t['id']}", headers=H(world), json=put_body(t, body="Draft {{ plan }}"))

    def prev(version):
        return world.client.post(f"{T}/{t['id']}/preview", headers=H(world), json={**SAMPLE, "version": version})

    assert (await prev("published")).json()["text"] == "Second pro"
    assert (await prev("1")).json()["text"] == "Welcome ada, you joined pro" and (await prev("1")).json()["version"] == "1"
    assert (await prev("9")).status_code == 404
    assert (await prev("latest-ish")).status_code == 422


async def test_previewing_published_before_anything_is_published_is_a_409(world):
    t = await create(world)
    r = await world.client.post(f"{T}/{t['id']}/preview", headers=H(world), json={**SAMPLE, "version": "published"})
    assert r.status_code == 409


async def test_a_template_that_does_not_render_says_so(world):
    t = await create(world, body="{% for x in %}")
    r = await world.client.post(f"{T}/{t['id']}/preview", headers=H(world), json=SAMPLE)
    assert r.status_code == 422 and "does not render" in r.text


async def test_preview_renders_markdown_to_html_and_uses_a_sample_recipient_by_default(world):
    t = await create(world, format="markdown", body="# Hi **{{ recipient.external_ref }}**")
    out = (await world.client.post(f"{T}/{t['id']}/preview", headers=H(world), json={})).json()
    assert "<h1>" in out["html"] and "sample-user" in out["html"]


async def test_preview_can_simulate_a_digest(world):
    t = await create(world, subject="{{ digest.count }} new",
                     body="{% for c in digest.items %}{{ c.text }};{% endfor %}")
    out = (await world.client.post(f"{T}/{t['id']}/preview", headers=H(world),
                                   json={"digest": [{"text": "a"}, {"text": "b"}]})).json()
    assert out["subject"] == "2 new" and out["text"] == "a;b;"


async def test_unsaved_text_can_be_previewed_too(world):
    r = await world.client.post(f"{T}/preview", headers=H(world), json={
        "channel": "sms", "body": "Code {{ code }} for {{ recipient.external_ref }}", "format": "text",
        "data": {"code": 1234}, "recipient": {"external_ref": "bo"}})
    assert r.status_code == 200 and r.json()["text"] == "Code 1234 for bo"
    bad = await world.client.post(f"{T}/preview", headers=H(world), json={"body": "{{ "})
    assert bad.status_code == 422


# ── workflows that use saved templates ─────────────────────────────────


def wf(definition, event_type="order.shipped", name="ship"):
    return {"name": name, "event_type": event_type, "definition": definition}


async def test_a_workflow_can_reference_a_published_template(world):
    await create(world, publish=True)
    r = await world.client.post("/api/v1/workflows", headers=H(world),
                                json=wf({"channels": [{"channel": "email", "template": "welcome"}]}))
    assert r.status_code == 201, r.text
    assert r.json()["definition"]["channels"] == [{"channel": "email", "template": "welcome"}]


@pytest.mark.parametrize("setup, message", [
    ("none", "Unknown template 'welcome'"),
    ("draft", "no published version"),
    ("wrong-channel", "Unknown template 'welcome' for channel 'email'"),
])
async def test_a_workflow_cannot_reference_an_unusable_template(world, setup, message):
    if setup == "draft":
        await create(world)
    if setup == "wrong-channel":
        await create(world, publish=True, channel="sms", subject=None)
    r = await world.client.post("/api/v1/workflows", headers=H(world),
                                json=wf({"channels": [{"channel": "email", "template": "welcome"}]}))
    assert r.status_code == 422 and message in r.text


async def test_a_workflow_cannot_mix_a_template_with_its_own_body(world):
    await create(world, publish=True)
    r = await world.client.post("/api/v1/workflows", headers=H(world),
                                json=wf({"channels": [{"channel": "email", "template": "welcome", "body": "x"}]}))
    assert r.status_code == 422 and "cannot also have its own body" in r.text


async def test_a_template_in_use_cannot_be_deleted_renamed_or_deactivated(world):
    t = await create(world, publish=True)
    await world.client.post("/api/v1/workflows", headers=H(world),
                            json=wf({"channels": [{"channel": "email", "template": "welcome"}]}))
    gone = await world.client.delete(f"{T}/{t['id']}", headers=H(world))
    renamed = await world.client.put(f"{T}/{t['id']}", headers=H(world), json=put_body(t, name="hello"))
    off = await world.client.put(f"{T}/{t['id']}", headers=H(world), json=put_body(t, is_active=False))
    assert (gone.status_code, renamed.status_code, off.status_code) == (409, 409, 409)
    assert "ship" in gone.text
    # editing the draft is always fine
    assert (await world.client.put(f"{T}/{t['id']}", headers=H(world), json=put_body(t, body="ok"))).status_code == 200


async def test_an_unused_template_can_be_deleted_with_its_history(world):
    t = await create(world, publish=True)
    assert (await world.client.delete(f"{T}/{t['id']}", headers=H(world))).status_code == 204
    assert (await world.client.get(f"{T}/{t['id']}/versions", headers=H(world))).status_code == 404


async def test_events_send_the_published_version(world):
    t = await create(world, publish=True, subject="Hello", body="Published for {{ recipient.external_ref }}")
    await world.client.post("/api/v1/workflows", headers=H(world),
                            json=wf({"channels": [{"channel": "email", "template": "welcome"}]}, "user.signup", "w"))
    await world.client.put(f"{T}/{t['id']}", headers=H(world), json=put_body(t, body="DRAFT"))
    r = await world.client.post("/api/v1/events", headers=H(world),
                                json={"event_type": "user.signup", "source": "t", "idempotency_key": "e1"})
    assert r.status_code == 202, r.text
    [attempt] = world.uow.delivery_attempts.items.values()
    assert attempt.body == "Published for u1" and attempt.subject == "Hello"


async def test_writing_templates_needs_admin_but_reading_and_previewing_need_only_read(world):
    from services.identity_service import IdentityService

    identity = IdentityService(lambda: world.uow)
    reader, _ = await identity.create_api_key(world.tenant_id, "reader", scopes=["read"])
    t = await create(world, publish=True)
    rh = {"X-API-Key": reader}
    assert (await world.client.get(f"{T}/{t['id']}/versions", headers=rh)).status_code == 200
    assert (await world.client.post(f"{T}/{t['id']}/preview", headers=rh, json={})).status_code == 200
    assert (await world.client.post(f"{T}/{t['id']}/publish", headers=rh)).status_code == 403
    assert (await world.client.post(f"{T}/{t['id']}/rollback", headers=rh, json={"version": 1})).status_code == 403
    assert (await world.client.post(f"{T}/preview", headers=rh, json={"body": "x"})).status_code == 403
