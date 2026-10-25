"""The body of an SSE stream: replay, live delivery, heartbeat and clean-up."""
from __future__ import annotations

import contextlib
import json
from dataclasses import dataclass

from api.v1.sse import stream_events


@dataclass
class Attempt:
    id: str
    notification_id: str
    subject: str = "Title"
    body: str = "Body"


class Subscription:
    """Yields scripted messages; `None` means "nothing arrived within the timeout"."""

    def __init__(self, script):
        self.script = list(script)
        self.timeouts: list[float] = []

    async def get(self, timeout: float):
        self.timeouts.append(timeout)
        return self.script.pop(0) if self.script else None


class Subscribe:
    def __init__(self, script):
        self.subscription = Subscription(script)
        self.entered = self.exited = False

    @contextlib.asynccontextmanager
    async def _cm(self):
        self.entered = True
        try:
            yield self.subscription
        finally:
            self.exited = True

    def __call__(self):
        return self._cm()

    def cm(self):
        return self._cm()


def disconnect_after(n: int):
    """is_disconnected() that turns True after n checks."""
    state = {"calls": 0}

    async def is_disconnected() -> bool:
        state["calls"] += 1
        return state["calls"] > n

    return is_disconnected


async def collect(**kw) -> list[str]:
    return [chunk async for chunk in stream_events(**kw)]


def parse(chunk: str) -> dict:
    fields = dict(line.split(": ", 1) for line in chunk.strip().splitlines())
    return {**fields, "data": json.loads(fields["data"])}


async def test_missed_notifications_replay_first_flagged_then_live_ones_follow():
    sub = Subscribe([json.dumps({"id": "live-1", "title": "Now", "body": "b"})])
    chunks = await collect(
        replay_attempts=[Attempt("a1", "n1"), Attempt("a2", "n2", subject="Second")],
        subscribe=sub.cm(), is_disconnected=disconnect_after(1),
    )
    replayed_1, replayed_2, live = (parse(c) for c in chunks)
    assert replayed_1["id"] == "a1" and replayed_1["event"] == "notification"
    assert replayed_1["data"] == {
        "id": "a1", "notification_id": "n1", "title": "Title", "body": "Body", "replayed": True,
    }
    assert replayed_2["data"]["title"] == "Second"
    assert live["id"] == "live-1" and "replayed" not in live["data"]   # live events are not replays


async def test_the_sse_id_is_the_attempt_id_so_last_event_id_can_resume():
    sub = Subscribe([json.dumps({"id": "attempt-9", "title": "t"})])
    [chunk] = await collect(replay_attempts=[], subscribe=sub.cm(), is_disconnected=disconnect_after(1))
    assert chunk.startswith("id: attempt-9\n")


async def test_an_idle_stream_sends_heartbeat_comments():
    sub = Subscribe([])  # nothing ever arrives
    chunks = await collect(
        replay_attempts=[], subscribe=sub.cm(), is_disconnected=disconnect_after(3), heartbeat_seconds=7.5,
    )
    assert chunks == [": ping\n\n"] * 3
    assert sub.subscription.timeouts == [7.5, 7.5, 7.5]


async def test_the_subscription_is_released_when_the_client_disconnects():
    sub = Subscribe([])
    await collect(replay_attempts=[], subscribe=sub.cm(), is_disconnected=disconnect_after(0))
    assert sub.entered and sub.exited


async def test_the_subscription_is_released_when_the_consumer_gives_up_early():
    sub = Subscribe([json.dumps({"id": "x"})] * 5)
    gen = stream_events(replay_attempts=[], subscribe=sub.cm(), is_disconnected=disconnect_after(100))
    await anext(gen)          # one event delivered...
    await gen.aclose()        # ...then the connection drops mid-stream
    assert sub.exited


async def test_with_nothing_to_replay_the_first_chunk_is_a_live_event_or_a_ping():
    sub = Subscribe([json.dumps({"id": "only"})])
    chunks = await collect(replay_attempts=[], subscribe=sub.cm(), is_disconnected=disconnect_after(2))
    assert [c.split("\n")[0] for c in chunks] == ["id: only", ": ping"]
