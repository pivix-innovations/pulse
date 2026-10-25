"""E2: in-app, wait, then email only if still unread -- across the real scheduler and workers."""
from __future__ import annotations

import uuid

import pytest

from tests.e2e.harness import Stack, wait_for


@pytest.fixture(scope="module")
def stack():
    s = Stack("sequences", redis_db=9).start()
    try:
        yield s
    except BaseException:
        print(s.dump_logs())
        raise
    finally:
        s.stop()


STEPS = [
    {"id": "nudge", "channel": "in_app", "subject": "Hi {{ recipient.external_ref }}", "body": "Open the app", "format": "text"},
    {"id": "mail", "channel": "email", "subject": "You missed this", "body": "Still here", "format": "text",
     "delay": 3, "when": {"status": "unread"}},
]


def send(stack, who, payload=None):
    r = stack.api("POST", "/events", json={
        "event_type": "order.placed", "source": "e2e", "idempotency_key": str(uuid.uuid4()),
        "payload": {"recipients": [{"external_ref": who, "email": f"{who}@example.com"}], **(payload or {})},
    })
    assert r.status_code == 202, r.text
    return r.json()["notification_id"]


def steps_of(stack, nid):
    return {s["step_id"]: s for s in stack.notification(nid)["steps"]}


def test_an_unread_message_gets_the_email_after_the_delay(stack):
    r = stack.api("POST", "/workflows", json={"name": "seq", "event_type": "order.placed", "definition": {"steps": STEPS}})
    assert r.status_code == 201, r.text

    nid = send(stack, "sleepy")
    n = wait_for(lambda: len(stack.notification(nid)["attempts"]) == 1 and stack.notification(nid),
                 what="the first step to be sent")
    assert n["attempts"][0]["channel"] == "in_app" and steps_of(stack, nid)["mail"]["status"] == "scheduled"

    wait_for(lambda: steps_of(stack, nid)["mail"]["status"] == "done", timeout=30, what="the email step to run")
    n = stack.notification(nid)
    assert {a["channel"] for a in n["attempts"]} == {"in_app", "email"}
    email = next(a for a in n["attempts"] if a["channel"] == "email")
    wait_for(lambda: next(a for a in stack.notification(nid)["attempts"] if a["channel"] == "email")["status"] == "sent",
             what="the email to be delivered to its provider")
    assert email["channel"] == "email"


def test_reading_the_message_in_time_cancels_the_email(stack):
    nid = send(stack, "keen")
    wait_for(lambda: stack.notification(nid)["attempts"] and stack.notification(nid)["attempts"][0]["status"] == "sent",
             what="the in-app message to be stored")
    [message] = stack.api("GET", "/inbox", params={"recipient": "keen"}).json()["items"]
    assert stack.api("POST", f"/inbox/{message['id']}/read", params={"recipient": "keen"}).status_code == 200

    wait_for(lambda: steps_of(stack, nid)["mail"]["status"] == "skipped", timeout=30, what="the email step to be skipped")
    n = wait_for(lambda: (n := stack.notification(nid))["status"] == "completed" and n, timeout=30, what="settlement")
    assert [a["channel"] for a in n["attempts"]] == ["in_app"]
    assert "delivered" in steps_of(stack, nid)["mail"]["reason"]


def test_a_payload_condition_decides_per_event(stack):
    stack.api("POST", "/workflows", json={"name": "vip", "event_type": "order.vip", "definition": {"steps": [
        {"id": "mail", "channel": "email", "subject": "Thanks", "body": "b", "format": "text"},
        {"id": "call", "channel": "sms", "body": "VIP thanks", "format": "text", "delay": 1,
         "when": {"payload": [{"field": "total", "op": "gte", "value": 100}]}},
    ]}})

    def go(who, total):
        r = stack.api("POST", "/events", json={
            "event_type": "order.vip", "source": "e2e", "idempotency_key": str(uuid.uuid4()),
            "payload": {"total": total, "recipients": [{"external_ref": who, "email": f"{who}@e.com", "phone": "+15550002222"}]},
        })
        return r.json()["notification_id"]

    small, big = go("small", 20), go("big", 250)
    for nid in (small, big):
        wait_for(lambda nid=nid: steps_of(stack, nid).get("call", {}).get("status") in ("done", "skipped"),
                 timeout=30, what="the second step to run")
    assert steps_of(stack, small)["call"]["status"] == "skipped"
    assert steps_of(stack, big)["call"]["status"] == "done"
    for nid in (small, big):
        wait_for(lambda nid=nid: stack.notification(nid)["status"] == "completed", timeout=30, what="settlement")
