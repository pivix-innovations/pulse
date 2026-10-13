"""H7: in-app delivery over SSE across real processes (worker publishes, API holds the stream)."""
from __future__ import annotations

import uuid

import pytest

from tests.e2e.harness import SseClient, Stack, wait_for


@pytest.fixture(scope="module")
def stack():
    s = Stack("sse", redis_db=11).start()
    try:
        yield s
    except BaseException:
        print(s.dump_logs())
        raise
    finally:
        s.stop()


def workflow(stack):
    r = stack.api("POST", "/workflows", json={
        "name": "inapp", "event_type": "user.inapp",
        "definition": {"channels": [
            {"channel": "in_app", "subject": "Hello {{ recipient.external_ref }}", "body": "Welcome aboard", "format": "text"},
        ]},
    })
    assert r.status_code == 201, r.text


def send(stack, recipient="ada"):
    r = stack.api("POST", "/events", json={
        "event_type": "user.inapp", "source": "e2e", "idempotency_key": str(uuid.uuid4()),
        "payload": {"recipients": [{"external_ref": recipient}]},
    })
    assert r.status_code == 202, r.text
    return r.json()["notification_id"]


def connected(stack, recipient="ada", **kw) -> SseClient:
    before = stack.sse_connections()
    client = SseClient(stack, recipient, **kw)
    wait_for(lambda: client.status == 200 and stack.sse_connections() > before,
             timeout=15, what="the SSE connection to register")
    return client


def attempt(stack, notification_id):
    [a] = stack.notification(notification_id)["attempts"]
    return a


def test_delivery_over_sse_is_confirmed_by_the_clients_ack(stack):
    workflow(stack)
    client = connected(stack)
    try:
        nid = send(stack)
        event = client.next_notification()             # worker process -> Redis -> API process -> client
        assert event["data"]["title"] == "Hello ada" and event["data"]["body"] == "Welcome aboard"
        assert event["data"]["notification_id"] == nid and event["id"] == event["data"]["id"]

        # Sent, but not delivered until the client says so.
        a = wait_for(lambda: attempt(stack, nid)["status"] == "sent" and attempt(stack, nid), what="attempt to be sent")
        assert a["awaiting_confirmation"] is True
        assert stack.notification(nid)["status"] != "completed"

        ack = stack.api("POST", f"/notifications/{nid}/ack", json={"attempt_id": event["id"]})
        assert ack.status_code == 200, ack.text
        n = wait_for(lambda: (n := stack.notification(nid))["status"] == "completed" and n, what="settlement")
        assert n["attempts"][0]["status"] == "delivered"
    finally:
        client.close()


def test_a_client_that_missed_a_notification_is_replayed_it_on_reconnect_until_it_acks(stack):
    first = connected(stack, "grace")
    nid = send(stack, "grace")
    event = first.next_notification()
    first.close()                                      # connection drops before the client acknowledged
    wait_for(lambda: stack.sse_connections() == 0, what="the dropped connection to be released")

    second = connected(stack, "grace", last_event_id=event["id"])
    try:
        replayed = second.next_notification()
        assert replayed["id"] == event["id"] and replayed["data"]["replayed"] is True
        assert replayed["data"]["notification_id"] == nid
        stack.api("POST", f"/notifications/{nid}/ack", json={"attempt_id": event["id"]})
    finally:
        second.close()

    third = connected(stack, "grace", last_event_id=event["id"])
    try:
        with pytest.raises(AssertionError):            # acknowledged: nothing left to replay
            third.next_notification(timeout=2)
    finally:
        third.close()


def inbox(stack, recipient, **params):
    r = stack.api("GET", "/inbox", params={"recipient": recipient, **params})
    assert r.status_code == 200, r.text
    return r.json()


def test_with_nobody_connected_the_message_waits_in_the_inbox_until_it_is_read(stack):
    nid = send(stack, "nobody-home")
    a = wait_for(lambda: attempt(stack, nid)["status"] == "sent" and attempt(stack, nid),
                 timeout=20, what="the attempt to be sent")
    assert a["attempt_count"] == 1 and a["awaiting_confirmation"] is True   # stored, not failed, not retried

    page = inbox(stack, "nobody-home")
    assert page["unread_count"] == 1
    [message] = page["items"]
    assert message["title"] == "Hello nobody-home" and message["notification_id"] == nid

    # The person comes online later and reads it: that is the confirmation.
    read = stack.api("POST", f"/inbox/{message['id']}/read", params={"recipient": "nobody-home"})
    assert read.status_code == 200 and read.json() == {"changed": 1, "unread_count": 0}
    n = wait_for(lambda: (n := stack.notification(nid))["status"] == "completed" and n, what="settlement")
    assert n["attempts"][0]["status"] == "delivered"


def test_a_live_event_and_the_inbox_describe_the_same_message(stack):
    client = connected(stack, "live-and-stored")
    try:
        nid = send(stack, "live-and-stored")
        event = client.next_notification()
        [message] = inbox(stack, "live-and-stored")["items"]
        assert event["data"]["inbox_id"] == message["id"]          # a client can mark exactly this one read
        stack.api("POST", f"/inbox/{message['id']}/read", params={"recipient": "live-and-stored"})
        wait_for(lambda: attempt(stack, nid)["status"] == "delivered", what="read to confirm delivery")
    finally:
        client.close()


def test_streams_are_private_to_the_recipient_they_were_opened_for(stack):
    ada = connected(stack, "ada")
    bob = connected(stack, "bob")
    try:
        send(stack, "bob")
        assert bob.next_notification()["data"]["title"] == "Hello bob"
        with pytest.raises(AssertionError):
            ada.next_notification(timeout=2)
    finally:
        ada.close()
        bob.close()
