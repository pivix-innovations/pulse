"""E3: many events, one message -- through the real API, scheduler, workers and Postgres."""
from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

from tests.e2e.harness import Stack, wait_for


@pytest.fixture(scope="module")
def stack():
    s = Stack("digests", redis_db=8).start()
    try:
        yield s
    except BaseException:
        print(s.dump_logs())
        raise
    finally:
        s.stop()


def comment_workflow(stack, window="3s", max_items=50, event_type="comment.added", key=None):
    digest = {"window": window, "max_items": max_items, **({"key": key} if key else {})}
    r = stack.api("POST", "/workflows", json={"name": event_type, "event_type": event_type, "definition": {
        "channels": [{"channel": "email",
                      "subject": "{% if digest.count > 1 %}{{ digest.count }} new comments{% else %}New comment{% endif %}",
                      "body": "{% for c in digest.items %}- {{ c.text }}\n{% endfor %}", "format": "text"}],
        "digest": digest,
    }})
    assert r.status_code == 201, r.text


def comment(stack, who, text, event_type="comment.added", **payload):
    r = stack.api("POST", "/events", json={
        "event_type": event_type, "source": "e2e", "idempotency_key": str(uuid.uuid4()),
        "payload": {"text": text, **payload, "recipients": [{"external_ref": who, "email": f"{who}@example.com"}]},
    })
    assert r.status_code == 202, r.text
    return r.json()["notification_id"]


def test_a_burst_of_events_becomes_one_email_after_the_window(stack):
    comment_workflow(stack)
    ids = [comment(stack, "dana", f"comment {i}") for i in range(4)]

    first = stack.notification(ids[0])
    assert first["attempts"] == [] and first["digest"]["status"] == "open" and first["digest"]["item_count"] == 4

    n = wait_for(lambda: len(stack.notification(ids[0])["attempts"]) == 1 and stack.notification(ids[0]),
                 timeout=30, what="the digest to be sent")
    [attempt] = n["attempts"]
    assert attempt["channel"] == "email"
    wait_for(lambda: stack.notification(ids[0])["attempts"][0]["status"] == "sent", what="the email to reach its provider")

    # the other three events produced no messages of their own, and are done
    for other in ids[1:]:
        o = wait_for(lambda other=other: (o := stack.notification(other))["status"] == "completed" and o,
                     timeout=30, what="merged notification to complete")
        assert o["attempts"] == [] and o["digest"]["is_anchor"] is False and o["digest"]["status"] == "flushed"
    wait_for(lambda: stack.notification(ids[0])["status"] == "completed", timeout=30, what="anchor to settle")

    [row] = stack.sql(
        "SELECT subject, body FROM delivery_attempts WHERE id = $1::uuid", attempt["id"])
    assert row["subject"] == "4 new comments"
    assert [line for line in row["body"].splitlines() if line] == [f"- comment {i}" for i in range(4)]


def test_events_arriving_at_the_same_moment_all_land_in_one_batch(stack):
    comment_workflow(stack, window="5s", event_type="burst.added")
    with ThreadPoolExecutor(max_workers=8) as pool:
        ids = list(pool.map(lambda i: comment(stack, "erin", f"c{i}", event_type="burst.added"), range(16)))
    batches = {stack.notification(n)["digest"]["batch_id"] for n in ids}
    assert len(batches) == 1                                   # no lost, split or duplicated batch
    # whichever event committed first anchors the batch (they were sent concurrently)
    anchor = stack.notification(ids[0])["digest"]["anchor_notification_id"]
    assert anchor in ids
    wait_for(lambda: stack.notification(anchor)["attempts"], timeout=30, what="the digest to be sent")
    [row] = stack.sql("SELECT subject FROM delivery_attempts WHERE id = $1::uuid",
                      stack.notification(anchor)["attempts"][0]["id"])
    assert row["subject"] == "16 new comments"


def test_reaching_max_items_sends_without_waiting_for_the_window(stack):
    comment_workflow(stack, window="1h", max_items=3, event_type="full.added")
    ids = [comment(stack, "finn", f"c{i}", event_type="full.added") for i in range(3)]
    wait_for(lambda: stack.notification(ids[0])["attempts"], timeout=30, what="the full digest to be sent early")


def test_a_key_keeps_different_subjects_apart(stack):
    comment_workflow(stack, window="3s", event_type="keyed.added", key="post")
    a1 = comment(stack, "gia", "on A", event_type="keyed.added", post="A")
    b1 = comment(stack, "gia", "on B", event_type="keyed.added", post="B")
    a2 = comment(stack, "gia", "on A again", event_type="keyed.added", post="A")
    assert stack.notification(a1)["digest"]["batch_id"] == stack.notification(a2)["digest"]["batch_id"]
    assert stack.notification(a1)["digest"]["batch_id"] != stack.notification(b1)["digest"]["batch_id"]
    for n in (a1, b1):
        wait_for(lambda n=n: stack.notification(n)["attempts"], timeout=30, what="digests to be sent")
