"""A1: the whole pipeline on real infrastructure, workers and breaker included."""
from __future__ import annotations

import uuid

import pytest

from tests.e2e.harness import Stack, wait_for


@pytest.fixture(scope="module")
def stack():
    s = Stack("smoke", redis_db=15).start()
    try:
        yield s
    except BaseException:
        print(s.dump_logs())
        raise
    finally:
        s.stop()


RECIPIENT = {"external_ref": "ada", "email": "ada@example.com", "phone": "+15550001111"}


def workflow(name, event_type, channels, chains=None, fallbacks=None):
    definition = {
        "channels": [{"channel": c, "subject": "Hi {{ recipient.external_ref }}", "body": "Hello", "format": "text"} for c in channels],
        "fallbacks": fallbacks or {},
    }
    if chains:
        definition["provider_chains"] = chains
    return {"name": name, "event_type": event_type, "definition": definition}


def send(stack, event_type, **extra):
    r = stack.api("POST", "/events", json={
        "event_type": event_type, "source": "e2e",
        "payload": {"recipients": [RECIPIENT]},
        "idempotency_key": str(uuid.uuid4()), **extra,
    })
    assert r.status_code == 202, r.text
    return r.json()["notification_id"]


def settled(stack, notification_id, timeout=30):
    return wait_for(
        lambda: (n := stack.notification(notification_id))["status"] in ("completed", "failed") and n,
        timeout=timeout, what=f"notification {notification_id} to settle",
    )


def test_event_flows_through_outbox_workers_and_settles(stack):
    r = stack.api("POST", "/workflows", json=workflow("smoke", "user.smoke", ["email", "sms"]))
    assert r.status_code == 201, r.text

    n = settled(stack, send(stack, "user.smoke"))
    assert n["status"] == "completed"
    by_channel = {a["channel"]: a for a in n["attempts"]}
    assert set(by_channel) == {"email", "sms"}
    for attempt in by_channel.values():
        assert attempt["status"] == "sent" and attempt["attempt_count"] == 1
        assert attempt["provider_message_id"].startswith("console-")  # env-default console providers


def test_duplicate_event_is_idempotent(stack):
    key = str(uuid.uuid4())
    body = {"event_type": "user.smoke", "source": "e2e", "payload": {"recipients": [RECIPIENT]}, "idempotency_key": key}
    first = stack.api("POST", "/events", json=body).json()["notification_id"]
    second = stack.api("POST", "/events", json=body).json()["notification_id"]
    assert first == second
    settled(stack, first)
    assert len(stack.notification(first)["attempts"]) == 2


def test_failover_trail_and_circuit_breaker_on_real_redis(stack):
    # A relay that refuses connections, then a working fallback.
    dead = stack.api("POST", "/providers", json={
        "channel": "email", "name": "smtp", "label": "Dead relay",
        "config": {"host": "127.0.0.1", "port": 1, "from_address": "a@example.com", "use_tls": False},
    })
    assert dead.status_code == 201, dead.text
    ok = stack.api("POST", "/providers", json={"channel": "email", "name": "console", "label": "Fallback inbox"})
    assert ok.status_code == 201, ok.text
    dead_id, ok_id = dead.json()["id"], ok.json()["id"]
    r = stack.api("POST", "/workflows", json=workflow(
        "failover", "user.failover", ["email"], chains={"email": [dead_id, ok_id]}))
    assert r.status_code == 201, r.text

    # Five failures in a row open the circuit (default threshold).
    for _ in range(5):
        n = settled(stack, send(stack, "user.failover"))
        [attempt] = n["attempts"]
        assert n["status"] == "completed" and attempt["provider_id"] == ok_id
        assert "Failed over to Fallback inbox after: Dead relay" in attempt["last_error"]

    def health():
        return {h["provider_id"]: h for h in stack.api("GET", "/providers/health").json()}

    h = wait_for(lambda: (h := health())[dead_id]["circuit"] == "open" and h, what="circuit to open")
    assert h[dead_id]["status"] == "down" and h[dead_id]["failures"] == 5
    assert h[ok_id]["status"] == "healthy"
    assert any(k.startswith("pulse:cb:open:") for k in stack.redis_keys("pulse:cb:*"))

    # The sixth message skips the dead relay entirely: no new failure recorded.
    n = settled(stack, send(stack, "user.failover"))
    assert "Dead relay: skipped (circuit open)" in n["attempts"][0]["last_error"]
    assert health()[dead_id]["failures"] == 5

