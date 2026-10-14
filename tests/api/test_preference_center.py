from __future__ import annotations

from domain.enums import ChannelType
from services.rendering_service import RenderingService
from services.unsubscribe import UnsubscribeLinks
from tests.api.test_events_notifications import EVENT

LINKS = UnsubscribeLinks("k" * 32, "https://pulse.test")
PC = "/api/v1/preference-center"


async def add_workflow(world, name, event_type, category):
    d = {"channels": [{"channel": "email", "subject": "s", "body": "b", "format": "text"}]}
    if category:
        d["category"] = category
    r = await world.client.post("/api/v1/workflows", headers=world.key_headers, json={"name": name, "event_type": event_type, "definition": d})
    assert r.status_code == 201, r.text


def test_preference_tokens_and_unsubscribe_tokens_are_not_interchangeable(world):
    pref = LINKS.preferences_url_for(world.tenant_id, "u1").split("token=")[1]
    assert LINKS.verify_preferences(pref) == (world.tenant_id, "u1")
    assert LINKS.verify(pref) is None                                  # not usable as an unsubscribe link
    unsub = LINKS.token(world.tenant_id, ChannelType.EMAIL, "a@b.co")
    assert LINKS.verify_preferences(unsub) is None                     # and the other way round
    assert LINKS.verify_preferences(pref + "x") is None
    assert UnsubscribeLinks("", None).preferences_url_for(world.tenant_id, "u1") is None


async def test_the_page_lists_the_tenants_categories_and_saves_choices(world):
    world.app.state.unsubscribe_links = LINKS
    await add_workflow(world, "a", "news.sent", "marketing")
    await add_workflow(world, "b", "bill.sent", "billing")
    await add_workflow(world, "c", "pw.reset", None)                    # no category: nothing to choose
    token = LINKS.preferences_url_for(world.tenant_id, "u1").split("token=")[1]

    page = await world.client.get(PC, params={"token": token})
    assert page.status_code == 200 and "Marketing" in page.text and "Billing" in page.text
    assert page.text.count("type=checkbox") == 2 and page.text.count(" checked") == 2    # on until they say otherwise

    saved = await world.client.post(PC, content=f"token={token}&cat_billing=on", headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert saved.status_code == 200 and "saved" in saved.text.lower()
    prefs = {p.category: p.opted_in for p in world.uow.category_preferences.items}
    assert prefs == {"billing": True, "marketing": False}               # unticked marketing = off

    again = await world.client.get(PC, params={"token": token})
    assert again.text.count(" checked") == 1


async def test_bad_tokens_and_disabled_links_are_404(world):
    world.app.state.unsubscribe_links = LINKS
    assert (await world.client.get(PC, params={"token": "nope"})).status_code == 404
    assert (await world.client.post(PC, content="token=nope")).status_code == 404
    unsub = LINKS.token(world.tenant_id, ChannelType.EMAIL, "a@b.co")
    assert (await world.client.get(PC, params={"token": unsub})).status_code == 404
    world.app.state.unsubscribe_links = UnsubscribeLinks("", None)
    good = LINKS.preferences_url_for(world.tenant_id, "u1").split("token=")[1]
    assert (await world.client.get(PC, params={"token": good})).status_code == 404


async def test_templates_can_use_preferences_url(world):
    world.app.state.notification_service._rendering = RenderingService(unsubscribe=LINKS)
    d = {"channels": [{"channel": "email", "subject": "s", "body": "Manage: {{ preferences_url }}", "format": "text"}]}
    await world.client.post("/api/v1/workflows", headers=world.key_headers, json={"name": "n", "event_type": "news.sent", "definition": d})
    await world.client.post("/api/v1/events", headers=world.key_headers, json={**EVENT, "event_type": "news.sent"})
    [attempt] = world.uow.delivery_attempts.items.values()
    url = attempt.body.split("Manage: ")[1]
    assert url.startswith("https://pulse.test/api/v1/preference-center?token=")
    assert LINKS.verify_preferences(url.split("token=")[1]) == (world.tenant_id, "u1")
