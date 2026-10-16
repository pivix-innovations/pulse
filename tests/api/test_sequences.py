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


# ── digests ────────────────────────────────────────────────────────────

DIGEST = {
    "channels": [{"channel": "email", "subject": "{{ digest.count }} new", "body": "b", "format": "text"}],
    "digest": {"window": "10m", "max_items": 5},
}


async def test_a_digest_workflow_is_accepted(world):
    res = await world.client.post("/api/v1/workflows", headers=world.key_headers, json=workflow(DIGEST, "c.added", "dg"))
    assert res.status_code == 201, res.text


@pytest.mark.parametrize("digest, message", [
    ({"window": "soon"}, "digest window"),
    ({"max_items": 5}, "window"),
    ({"window": "1m", "max_items": 1}, "max_items"),
])
async def test_a_bad_digest_is_rejected_with_the_reason(world, digest, message):
    res = await world.client.post("/api/v1/workflows", headers=world.key_headers,
                                  json=workflow({**DIGEST, "digest": digest}, "c.added", "dg"))
    assert res.status_code == 422 and message in res.text


async def test_a_workflow_cannot_be_both_a_digest_and_a_sequence(world):
    res = await world.client.post("/api/v1/workflows", headers=world.key_headers, json=workflow(
        {"steps": STEPS, "digest": {"window": "1m"}}, "c.added", "both"))
    assert res.status_code == 422 and "not both" in res.text


async def test_a_buffered_notification_reports_its_digest(world):
    await world.client.post("/api/v1/workflows", headers=world.key_headers, json=workflow(DIGEST, "user.signup", "dg"))
    ids = []
    for i in range(2):
        r = await world.client.post("/api/v1/events", headers=world.key_headers, json={
            "event_type": "user.signup", "source": "t", "idempotency_key": f"d{i}"})
        assert r.status_code == 202, r.text
        ids.append(r.json()["notification_id"])
    first, second = [
        (await world.client.get(f"/api/v1/notifications/{n}", headers=world.key_headers)).json() for n in ids
    ]
    assert first["attempts"] == [] and first["status"] == "processing"
    assert first["digest"]["status"] == "open" and first["digest"]["item_count"] == 2
    assert first["digest"]["is_anchor"] is True and second["digest"]["is_anchor"] is False
    assert second["digest"]["anchor_notification_id"] == ids[0]
    assert first["digest"]["batch_id"] == second["digest"]["batch_id"]


async def test_an_operator_can_cancel_the_steps_that_have_not_run_yet(world):
    await world.client.post("/api/v1/workflows", headers=world.key_headers, json=workflow({"steps": STEPS}))
    nid = (await world.client.post("/api/v1/events", headers=world.key_headers, json={
        "event_type": "order.placed", "source": "t", "idempotency_key": "k-cancel",
    })).json()["notification_id"]
    url = f"/api/v1/notifications/{nid}/cancel-sequence"

    first = await world.client.post(url, headers=world.key_headers)
    assert first.status_code == 200 and first.json()["cancelled"] == 1

    detail = (await world.client.get(f"/api/v1/notifications/{nid}", headers=world.key_headers)).json()
    steps = {s["step_id"]: s for s in detail["steps"]}
    assert steps["nudge"]["status"] == "done"                      # what already ran stays as it was
    assert steps["mail"]["status"] == "skipped" and steps["mail"]["reason"] == "cancelled by an operator"
    assert [a["channel"] for a in detail["attempts"]] == ["in_app"]

    again = await world.client.post(url, headers=world.key_headers)
    assert again.status_code == 200 and again.json()["cancelled"] == 0


async def test_cancelling_an_unknown_notification_is_404(world):
    r = await world.client.post("/api/v1/notifications/00000000-0000-0000-0000-000000000000/cancel-sequence", headers=world.key_headers)
    assert r.status_code == 404
