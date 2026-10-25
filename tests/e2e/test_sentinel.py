"""C2 on the real stack: Pulse resolving recipients through Sentinel, and surviving its outages."""
from __future__ import annotations

import time
import uuid

import pytest

from tests.e2e.harness import Stack, wait_for
from tests.e2e.mock_sentinel import MockSentinel


@pytest.fixture(scope="module")
def sentinel():
    s = MockSentinel().start()
    yield s
    s.stop()


@pytest.fixture(scope="module")
def stack(sentinel):
    s = Stack("sentinel", redis_db=9, env={
        "PULSE_SENTINEL__BASE_URL": sentinel.url,
        "PULSE_SENTINEL__TOKEN": sentinel.token,
        "PULSE_SENTINEL__CACHE_TTL_SECONDS": "0.3",
        "PULSE_SENTINEL__NEGATIVE_CACHE_TTL_SECONDS": "0.3",
        "PULSE_SENTINEL__RETRIES": "1",
        "PULSE_SENTINEL__RETRY_BACKOFF_SECONDS": "0.05",
        "PULSE_SENTINEL__BREAKER_FAILURE_THRESHOLD": "50",     # exercise fallbacks, not the breaker
        "PULSE_SENTINEL__READ_TIMEOUT_SECONDS": "1",
    }).start()
    r = s.api("POST", "/workflows", json={
        "name": "welcome", "event_type": "user.signup",
        "definition": {"channels": [{"channel": "email", "subject": "Hi {{ recipient.external_ref }}", "body": "Hello", "format": "text"}]},
    })
    assert r.status_code == 201, r.text
    try:
        yield s
    except BaseException:
        print(s.dump_logs())
        raise
    finally:
        s.stop()


@pytest.fixture(autouse=True)
def healthy(sentinel):
    sentinel.mode = "up"
    sentinel.calls.clear()
    time.sleep(0.4)                       # let any cached answer expire
    yield


def send(stack, user, key=None):
    return stack.api("POST", "/events", json={
        "event_type": "user.signup", "source": "e2e", "idempotency_key": key or str(uuid.uuid4()),
        "payload": {"user_id": user},
    })


def delivered_to(stack, nid):
    n = wait_for(lambda: (n := stack.notification(nid))["status"] == "completed" and n, what="settlement")
    return n["attempts"]


def test_recipients_come_from_sentinel_with_the_agreed_request(stack, sentinel):
    r = send(stack, "ada")
    assert r.status_code == 202, r.text
    assert delivered_to(stack, r.json()["notification_id"])[0]["status"] == "sent"

    [call] = sentinel.calls
    assert call["auth"] == f"Bearer {sentinel.token}" and call["tenant"] == stack.tenant_id
    assert call["body"]["event_type"] == "user.signup" and call["body"]["payload"] == {"user_id": "ada"}

    # The local snapshot now mirrors what Sentinel said (the outage fallback).
    [row] = stack.sql("SELECT email, locale, timezone FROM recipients WHERE external_ref = 'ada'")
    assert (row["email"], row["locale"], row["timezone"]) == ("ada@example.com", "fr", "Europe/Paris")


def test_a_group_answer_fans_out_to_everyone_sentinel_names(stack):
    r = send(stack, "everyone")
    assert r.status_code == 202
    assert len(delivered_to(stack, r.json()["notification_id"])) == 2


def test_sentinel_saying_nobody_is_a_422(stack):
    r = send(stack, "stranger")
    assert r.status_code == 422 and "No recipients" in r.json()["detail"]


def test_a_known_recipient_is_still_served_during_an_outage(stack, sentinel):
    assert send(stack, "ada").status_code == 202            # warms the cache and the local snapshot
    sentinel.mode = "down"
    time.sleep(0.4)                                          # cache expired: stale-if-error / snapshot must carry it
    r = send(stack, "ada")
    assert r.status_code == 202, r.text
    assert delivered_to(stack, r.json()["notification_id"])[0]["status"] == "sent"


def test_an_unknown_recipient_during_an_outage_is_a_503_to_retry_and_recovers(stack, sentinel):
    sentinel.mode = "down"
    r = send(stack, "dana", key="dana-1")                    # never seen before: no cache, no snapshot
    assert r.status_code == 503 and r.headers["Retry-After"] == "5"
    sentinel.mode = "up"
    retry = send(stack, "dana", key="dana-1")
    assert retry.status_code == 202                          # the producer's retry works


def test_a_resent_event_is_answered_without_asking_sentinel_even_when_it_is_down(stack, sentinel):
    key = str(uuid.uuid4())
    first = send(stack, "ada", key=key)
    assert first.status_code == 202
    sentinel.mode = "down"
    sentinel.calls.clear()
    again = send(stack, "someone-new", key=key)              # same key: answered from Pulse's own records
    assert again.status_code == 202 and again.json() == first.json()
    assert sentinel.calls == []


def test_rejected_credentials_surface_as_unavailable_not_as_nobody(stack, sentinel):
    sentinel.token = "rotated-by-someone-else"
    try:
        r = send(stack, "erin")
        assert r.status_code == 503, r.text
    finally:
        sentinel.token = "sentinel-token"
