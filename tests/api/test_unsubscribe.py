from __future__ import annotations

from domain.enums import ChannelType
from services.rendering_service import RenderingService
from services.unsubscribe import UnsubscribeLinks
from tests.api.test_events_notifications import EVENT

LINKS = UnsubscribeLinks("k" * 32, "https://pulse.test")


def test_token_round_trips_and_rejects_tampering_and_other_keys(world):
    t = LINKS.token(world.tenant_id, ChannelType.EMAIL, "Ada@Example.com")
    assert LINKS.verify(t) == (world.tenant_id, ChannelType.EMAIL, "ada@example.com")
    body, sig = t.split(".")
    assert LINKS.verify(f"{body}x.{sig}") is None
    assert LINKS.verify("garbage") is None
    assert UnsubscribeLinks("other-key", "https://pulse.test").verify(t) is None
    assert UnsubscribeLinks("", "https://pulse.test").url_for(world.tenant_id, ChannelType.EMAIL, "a@b.c") is None


async def test_confirm_page_then_post_suppresses_and_is_idempotent(world):
    world.app.state.unsubscribe_links = LINKS
    token = LINKS.token(world.tenant_id, ChannelType.EMAIL, "u1@example.com")

    page = await world.client.get("/api/v1/unsubscribe", params={"token": token})
    assert page.status_code == 200 and "u1@example.com" in page.text
    assert not await world.uow.suppressions.is_suppressed(world.tenant_id, ChannelType.EMAIL, "u1@example.com")

    for _ in range(2):
        done = await world.client.post("/api/v1/unsubscribe", data={"token": token})
        assert done.status_code == 200 and "unsubscribed" in done.text
    assert [s.reason.value for s in world.uow.suppressions.items] == ["unsubscribe"]


async def test_one_click_post_with_the_token_in_the_url(world):
    world.app.state.unsubscribe_links = LINKS
    token = LINKS.token(world.tenant_id, ChannelType.SMS, "+1 555 000 3333")
    r = await world.client.post(
        "/api/v1/unsubscribe", params={"token": token}, content="List-Unsubscribe=One-Click",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert r.status_code == 200
    assert await world.uow.suppressions.is_suppressed(world.tenant_id, ChannelType.SMS, "+15550003333")


async def test_bad_token_and_disabled_links_are_404(world):
    world.app.state.unsubscribe_links = LINKS
    assert (await world.client.get("/api/v1/unsubscribe", params={"token": "nope"})).status_code == 404
    assert (await world.client.post("/api/v1/unsubscribe", data={"token": "nope"})).status_code == 404
    world.app.state.unsubscribe_links = UnsubscribeLinks("", None)
    good = LINKS.token(world.tenant_id, ChannelType.EMAIL, "u1@example.com")
    assert (await world.client.post("/api/v1/unsubscribe", data={"token": good})).status_code == 404


async def test_templates_can_use_unsubscribe_url(world):
    service = world.app.state.notification_service
    service._rendering = RenderingService(unsubscribe=LINKS)
    h = world.key_headers
    r = await world.client.post("/api/v1/workflows", headers=h, json={
        "name": "news", "event_type": "news.sent",
        "definition": {"channels": [
            {"channel": "email", "subject": "News", "body": "Hi. Leave: {{ unsubscribe_url }}", "format": "text"},
        ]},
    })
    assert r.status_code == 201, r.text
    nid = (await world.client.post("/api/v1/events", headers=h, json={**EVENT, "event_type": "news.sent"})).json()["notification_id"]
    [attempt] = [a for a in world.uow.delivery_attempts.items.values() if str(a.notification_id) == nid]
    url = attempt.body.split("Leave: ")[1]
    assert url.startswith("https://pulse.test/api/v1/unsubscribe?token=")
    assert LINKS.verify(url.split("token=")[1])[2] == "u1@example.com"
