"""
A2: crash and loss scenarios on real infrastructure.

  * a worker is SIGKILLed mid-send  -> the attempt is recovered
  * a queued job is lost            -> the attempt is recovered
  * both end with exactly one successful send via the operator's re-route
"""
from __future__ import annotations

import asyncio
import signal
import threading
import uuid

import pytest

from jobqueue.producer import EMAIL_QUEUE
from tests.e2e.harness import Stack, wait_for

# Short leases so recovery takes seconds, not minutes. Order matters:
# dispatch lease (4s) < stuck TTLs (8s), as in production (180s < 300s).
CHAOS_ENV = {
    "PULSE_RETRY__DISPATCH_LEASE_SECONDS": "4",
    "PULSE_RETRY__PROVIDER_TIMEOUT_SECONDS": "120",
    "PULSE_RECONCILIATION__STUCK_IN_DOUBT_TTL_SECONDS": "8",
    "PULSE_RECONCILIATION__STUCK_LOST_TTL_SECONDS": "8",
}


class BlackHole:
    """A TCP server that accepts connections and never says a word (a hung SMTP relay)."""

    def __init__(self) -> None:
        self.port = 0
        self._loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        server = self._loop.run_until_complete(
            asyncio.start_server(lambda r, w: None, "127.0.0.1", 0)
        )
        self.port = server.sockets[0].getsockname()[1]
        self._ready.set()
        self._loop.run_forever()

    def __enter__(self):
        self._thread.start()
        self._ready.wait(5)
        return self

    def __exit__(self, *exc):
        self._loop.call_soon_threadsafe(self._loop.stop)


@pytest.fixture(scope="module")
def stack():
    s = Stack("chaos", redis_db=14, env=CHAOS_ENV).start()
    try:
        yield s
    except BaseException:
        print(s.dump_logs())
        raise
    finally:
        s.stop()


def make_provider(stack, **body):
    r = stack.api("POST", "/providers", json=body)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def make_workflow(stack, event_type, chain):
    r = stack.api("POST", "/workflows", json={
        "name": event_type, "event_type": event_type,
        "definition": {
            "channels": [{"channel": "email", "subject": "s", "body": "b", "format": "text"}],
            "provider_chains": {"email": chain},
        },
    })
    assert r.status_code == 201, r.text
    return r.json()


def send(stack, event_type):
    r = stack.api("POST", "/events", json={
        "event_type": event_type, "source": "e2e", "idempotency_key": str(uuid.uuid4()),
        "payload": {"recipients": [{"external_ref": "ada", "email": "ada@example.com"}]},
    })
    assert r.status_code == 202, r.text
    return r.json()["notification_id"]


def attempt_row(stack, notification_id):
    [row] = stack.sql(
        "SELECT status, dispatch_started_at, provider_message_id, attempt_count "
        "FROM delivery_attempts WHERE notification_id = $1::uuid", notification_id,
    )
    return row


def test_worker_killed_mid_send_is_recovered_after_the_lease_expires(stack):
    with BlackHole() as hole:
        hung = make_provider(
            stack, channel="email", name="smtp", label="Hung relay",
            config={"host": "127.0.0.1", "port": hole.port, "from_address": "a@example.com", "use_tls": False},
        )
        good = make_provider(stack, channel="email", name="console", label="Backup inbox")
        workflow = make_workflow(stack, "chaos.kill", [hung])

        notification_id = send(stack, "chaos.kill")

        # The worker claims the attempt (committed before the send) and then hangs in the provider.
        wait_for(
            lambda: attempt_row(stack, notification_id)["dispatch_started_at"] is not None,
            timeout=20, what="the worker to claim the attempt",
        )
        stack.kill("email", signal.SIGKILL)  # no cleanup, no goodbye

        row = attempt_row(stack, notification_id)
        assert row["status"] == "pending" and row["dispatch_started_at"] is not None  # outcome unknown

        # Operator re-routes email away from the dead relay, then brings a worker back.
        r = stack.api("PUT", f"/workflows/{workflow['id']}", json={
            "name": workflow["name"], "event_type": workflow["event_type"],
            "definition": {**workflow["definition"], "provider_chains": {"email": [good]}},
        })
        assert r.status_code == 200, r.text
        stack.restart_worker("email")

        n = wait_for(
            lambda: (n := stack.notification(notification_id))["status"] == "completed" and n,
            timeout=60, what="the stuck attempt to be requeued and delivered",
        )
        [attempt] = n["attempts"]
        assert attempt["status"] == "sent" and attempt["provider_id"] == good
        assert "Requeued stuck attempt" in stack.log("scheduler")

        # One successful send, nothing recorded against the relay that never answered.
        calls = stack.sql(
            "SELECT outcome FROM provider_calls WHERE attempt_id = $1::uuid", uuid.UUID(attempt["id"])
        )
        assert [c["outcome"] for c in calls] == ["ok"]


def test_a_lost_queue_job_is_recovered(stack):
    inbox = make_provider(stack, channel="email", name="console", label="Inbox B")
    make_workflow(stack, "chaos.lost", [inbox])

    stack.kill("email", signal.SIGTERM)       # nobody is consuming
    notification_id = send(stack, "chaos.lost")
    wait_for(
        lambda: attempt_row(stack, notification_id)["status"] == "pending"
        and stack.redis_keys(EMAIL_QUEUE),  # arq keeps the queue in a sorted set named after it
        timeout=20, what="the job to be queued",
    )

    # The queue loses the job (Redis flushed, failed over to an empty replica...).
    async def drop():
        from redis.asyncio import Redis

        r = stack.settings.redis
        redis = Redis(host=r.host, port=r.port, db=stack.redis_db, password=r.password)
        try:
            await redis.delete(EMAIL_QUEUE)
            await redis.delete(*[k async for k in redis.scan_iter("arq:job:*")])
        finally:
            await redis.aclose()

    asyncio.run(drop())
    stack.restart_worker("email")

    n = wait_for(
        lambda: (n := stack.notification(notification_id))["status"] == "completed" and n,
        timeout=60, what="the lost job to be re-published and delivered",
    )
    assert n["attempts"][0]["status"] == "sent"
    assert attempt_row(stack, notification_id)["attempt_count"] == 1  # delivered once
