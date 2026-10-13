"""Sequence workflows through the API: validation and the step progress on a notification."""
from __future__ import annotations

import pytest

STEPS = [
    {"id": "nudge", "channel": "in_app", "subject": "Hi", "body": "Open the app", "format": "text"},
    {"id": "mail", "channel": "email", "subject": "Missed", "body": "Still here", "format": "text",
     "delay": "10m", "when": {"status": "unread"}},
]


def workflow(definition, event_type="order.placed", name="seq"):
    return {"name": name, "event_type": event_type, "definition": definition}


async def test_a_sequence_workflow_is_accepted_and_stored_as_written(world):
    res = await world.client.post("/api/v1/workflows", headers=world.key_headers, json=workflow({"steps": STEPS}))
    assert res.status_code == 201, res.text
    assert res.json()["definition"]["steps"][1]["delay"] == "10m"


@pytest.mark.parametrize("definition, message", [
    ({"steps": []}, "at least one channel"),    # an empty list is not a sequence
    ({"steps": STEPS[:1] + [{**STEPS[1], "delay": "soon"}]}, "delay"),
    ({"steps": [STEPS[0], {**STEPS[1], "channel": "in_app"}]}, "more than one step"),
    ({"steps": [STEPS[0], {**STEPS[1], "when": {"step": "ghost", "status": "unread"}}]}, "not an earlier step"),
    ({"steps": [STEPS[0], {**STEPS[1], "when": {"status": "sort-of"}}]}, "unknown status"),
    ({"steps": STEPS, "channels": [{"channel": "sms", "body": "b"}]}, "not both"),
    ({"steps": STEPS, "fallbacks": {"in_app": "email"}}, "not both"),
])
async def test_a_malformed_sequence_is_rejected_with_the_reason(world, definition, message):
    res = await world.client.post("/api/v1/workflows", headers=world.key_headers, json=workflow(definition))
    assert res.status_code == 422, res.text
    assert message in res.text


async def test_a_notification_shows_each_recipients_progress_through_the_steps(world):
    created = await world.client.post("/api/v1/workflows", headers=world.key_headers, json=workflow({"steps": STEPS}))
    assert created.status_code == 201
    r = await world.client.post("/api/v1/events", headers=world.key_headers, json={
        "event_type": "order.placed", "source": "t", "idempotency_key": "k1",
    })
    assert r.status_code == 202, r.text
    detail = (await world.client.get(f"/api/v1/notifications/{r.json()['notification_id']}", headers=world.key_headers)).json()

    assert [a["channel"] for a in detail["attempts"]] == ["in_app"]
    steps = {s["step_id"]: s for s in detail["steps"]}
    assert steps["nudge"]["status"] == "done" and steps["nudge"]["attempt_id"] == detail["attempts"][0]["id"]
    assert steps["mail"]["status"] == "scheduled" and steps["mail"]["attempt_id"] is None
    assert steps["mail"]["run_at"] > steps["nudge"]["run_at"]
    assert detail["status"] == "processing"        # a step is still to run


async def test_classic_notifications_have_no_steps(world):
    r = await world.client.post("/api/v1/events", headers=world.key_headers, json={
        "event_type": "user.signup", "source": "t", "idempotency_key": "k2",
    })
    detail = (await world.client.get(f"/api/v1/notifications/{r.json()['notification_id']}", headers=world.key_headers)).json()
    assert detail["steps"] == []
