"""E4 on the real stack: workflows send the PUBLISHED version of a saved template."""
from __future__ import annotations

import uuid

import pytest

from tests.e2e.harness import Stack, wait_for


@pytest.fixture(scope="module")
def stack():
    s = Stack("templates", redis_db=5).start()
    try:
        yield s
    except BaseException:
        print(s.dump_logs())
        raise
    finally:
        s.stop()


def send(stack, who="ada", locale=None):
    r = stack.api("POST", "/events", json={
        "event_type": "order.shipped", "source": "e2e", "idempotency_key": str(uuid.uuid4()),
        "payload": {"item": "kettle", "recipients": [{"external_ref": who, "email": f"{who}@example.com", **({"locale": locale} if locale else {})}]},
    })
    assert r.status_code == 202, r.text
    return r.json()["notification_id"]


def delivered_text(stack, nid):
    a = wait_for(lambda: stack.notification(nid)["attempts"] and stack.notification(nid)["attempts"][0],
                 what="the message to be created")
    [row] = stack.sql("SELECT subject, body FROM delivery_attempts WHERE id = $1::uuid", a["id"])
    return row["subject"], row["body"]


def test_publish_edit_republish_and_rollback_change_what_goes_out(stack):
    t = stack.api("POST", "/templates", json={
        "name": "shipped", "channel": "email", "subject": "Shipped: {{ item }}",
        "body": "Hi {{ recipient.external_ref }}, your {{ item }} is on its way.", "format": "text", "publish": True}).json()
    wf = stack.api("POST", "/workflows", json={"name": "ship", "event_type": "order.shipped",
                                               "definition": {"channels": [{"channel": "email", "template": "shipped"}]}})
    assert wf.status_code == 201, wf.text

    assert delivered_text(stack, send(stack)) == ("Shipped: kettle", "Hi ada, your kettle is on its way.")

    # edit only the draft: live messages are unchanged, preview shows the draft
    body = {**{k: t[k] for k in ("name", "channel", "subject", "format", "locale", "is_active")}, "body": "NEW COPY for {{ item }}"}
    assert stack.api("PUT", f"/templates/{t['id']}", json=body).status_code == 200
    assert delivered_text(stack, send(stack, "bo"))[1] == "Hi bo, your kettle is on its way."
    prev = stack.api("POST", f"/templates/{t['id']}/preview", json={"data": {"item": "lamp"}}).json()
    assert prev["text"] == "NEW COPY for lamp" and prev["version"] == "draft"

    # publish: new messages use it
    assert stack.api("POST", f"/templates/{t['id']}/publish", json={"note": "rewrite"}).json()["published_version"] == 2
    assert delivered_text(stack, send(stack, "cy"))[1] == "NEW COPY for kettle"

    # roll back: new messages use the original again, history keeps everything
    rolled = stack.api("POST", f"/templates/{t['id']}/rollback", json={"version": 1}).json()
    assert rolled["published_version"] == 3
    assert delivered_text(stack, send(stack, "di"))[1] == "Hi di, your kettle is on its way."
    versions = stack.api("GET", f"/templates/{t['id']}/versions").json()
    assert [v["version"] for v in versions] == [3, 2, 1]
    assert stack.api("DELETE", f"/templates/{t['id']}").status_code == 409     # the workflow uses it


def test_the_recipients_language_picks_the_translation(stack):
    for loc, text in (("en", "Your order shipped"), ("es", "Su pedido ha sido enviado")):
        r = stack.api("POST", "/templates", json={"name": "ship-i18n", "channel": "sms", "body": text,
                                                  "format": "text", "locale": loc, "publish": True})
        assert r.status_code == 201, r.text
    stack.api("POST", "/workflows", json={"name": "ship-sms", "event_type": "order.shipped-sms",
                                          "definition": {"channels": [{"channel": "sms", "template": "ship-i18n"}]}})

    def go(who, locale):
        r = stack.api("POST", "/events", json={
            "event_type": "order.shipped-sms", "source": "e2e", "idempotency_key": str(uuid.uuid4()),
            "payload": {"recipients": [{"external_ref": who, "phone": "+15550003333", "locale": locale}]}})
        assert r.status_code == 202, r.text
        return delivered_text(stack, r.json()["notification_id"])[1]

    assert go("maria", "es") == "Su pedido ha sido enviado"
    assert go("john", "en") == "Your order shipped"
    assert go("hans", "de") == "Your order shipped"           # no German: falls back to English


def test_a_template_that_cannot_be_used_is_refused_at_save_time(stack):
    draft = stack.api("POST", "/templates", json={"name": "unfinished", "channel": "email", "body": "wip", "subject": "s"}).json()
    assert draft["published_version"] is None
    r = stack.api("POST", "/workflows", json={"name": "bad", "event_type": "x.y",
                                              "definition": {"channels": [{"channel": "email", "template": "unfinished"}]}})
    assert r.status_code == 422 and "no published version" in r.text
