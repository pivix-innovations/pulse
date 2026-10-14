from __future__ import annotations


from domain.enums import ChannelType
from services.rendering_service import RenderingService
from services.unsubscribe import UnsubscribeLinks
from tests.api.test_events_notifications import EVENT

CH = "/api/v1/category-preferences"
LINKS = UnsubscribeLinks("k" * 32, "https://pulse.test")


def workflow(category=None, channels=("email", "in_app")):
    definition = {"channels": [{"channel": c, "subject": "s", "body": "b {{ unsubscribe_url }}", "format": "text"} for c in channels]}
    if category:
        definition["category"] = category
    return {"name": "promo", "event_type": "promo.sent", "definition": definition}


async def send(world, key="k"):
    r = await world.client.post("/api/v1/events", headers=world.key_headers, json={**EVENT, "event_type": "promo.sent", "idempotency_key": key})
    assert r.status_code == 202, r.text
    detail = (await world.client.get(f"/api/v1/notifications/{r.json()['notification_id']}", headers=world.key_headers)).json()
    return {a["channel"] for a in detail["attempts"]}


async def set_pref(world, **body):
    return await world.client.put(CH, headers=world.key_headers, json={"external_ref": "u1", **body})


async def test_category_must_be_a_valid_slug(world):
    bad = await world.client.post("/api/v1/workflows", headers=world.key_headers, json=workflow("Not Valid!"))
    assert bad.status_code == 422
    assert (await set_pref(world, category="Bad Cat", opted_in=False)).status_code == 422
    ok = await world.client.post("/api/v1/workflows", headers=world.key_headers, json=workflow("marketing"))
    assert ok.status_code == 201 and ok.json()["definition"]["category"] == "marketing"


async def test_opting_out_of_a_category_blocks_only_that_category(world):
    await world.client.post("/api/v1/workflows", headers=world.key_headers, json=workflow("marketing"))
    assert await send(world, "a") == {"email", "in_app"}

    r = await set_pref(world, category="marketing", opted_in=False)
    assert r.status_code == 200 and r.json()["channel"] == "all" and r.json()["opted_in"] is False
    assert await send(world, "b") == set()

    await set_pref(world, category="billing", opted_in=False)            # a different category: no effect on this workflow
    await set_pref(world, category="marketing", opted_in=True)           # changed their mind (upsert, not duplicate)
    assert await send(world, "c") == {"email", "in_app"}
    assert len((await world.client.get(CH, params={"recipient": "u1"}, headers=world.key_headers)).json()) == 2


async def test_a_channel_specific_choice_beats_the_all_channels_one(world):
    await world.client.post("/api/v1/workflows", headers=world.key_headers, json=workflow("marketing"))
    await set_pref(world, category="marketing", opted_in=False)                       # off everywhere...
    await set_pref(world, category="marketing", channel="in_app", opted_in=True)      # ...except in-app
    assert await send(world) == {"in_app"}


async def test_workflows_without_a_category_ignore_category_preferences(world):
    await world.client.post("/api/v1/workflows", headers=world.key_headers, json=workflow(None))
    await set_pref(world, category="marketing", opted_in=False)
    assert await send(world) == {"email", "in_app"}


async def test_deleting_the_choice_resubscribes(world):
    await world.client.post("/api/v1/workflows", headers=world.key_headers, json=workflow("marketing"))
    pid = (await set_pref(world, category="marketing", opted_in=False)).json()["id"]
    assert await send(world, "a") == set()
    assert (await world.client.delete(f"{CH}/{pid}", headers=world.key_headers)).status_code == 204
    assert (await world.client.delete(f"{CH}/{pid}", headers=world.key_headers)).status_code == 404
    assert await send(world, "b") == {"email", "in_app"}


async def test_category_unsubscribe_link_opts_out_the_category_not_the_address(world):
    world.app.state.unsubscribe_links = LINKS
    world.app.state.notification_service._rendering = RenderingService(unsubscribe=LINKS)
    await world.client.post("/api/v1/workflows", headers=world.key_headers, json=workflow("marketing", channels=("email",)))
    await send(world)
    [attempt] = world.uow.delivery_attempts.items.values()
    url = attempt.body.split("b ")[1]
    target = LINKS.verify(url.split("token=")[1])
    assert (target.category, target.recipient_ref, target.channel) == ("marketing", "u1", ChannelType.EMAIL)

    token = url.split("token=")[1]
    page = await world.client.get("/api/v1/unsubscribe", params={"token": token})
    assert "marketing" in page.text
    done = await world.client.post("/api/v1/unsubscribe", params={"token": token})
    assert done.status_code == 200
    assert world.uow.suppressions.items == []                                   # the address is not blocked...
    [pref] = world.uow.category_preferences.items
    assert (pref.category, pref.channel, pref.opted_in) == ("marketing", "email", False)
    assert await send(world, "later") == set()                                  # this category's email is off for them


async def test_a_suppressed_address_skips_a_sequence_step_with_the_reason(world):
    steps = [{"id": "mail", "channel": "email", "subject": "s", "body": "b", "format": "text"}]
    r = await world.client.post("/api/v1/workflows", headers=world.key_headers, json={
        "name": "seq", "event_type": "promo.sent", "definition": {"steps": steps, "category": "marketing"},
    })
    assert r.status_code == 201, r.text
    await world.client.post("/api/v1/suppressions", headers=world.key_headers, json={"channel": "email", "address": "u1@example.com"})
    await send(world)
    [run] = world.uow.step_runs.items.values() if hasattr(world.uow.step_runs.items, "values") else world.uow.step_runs.items
    assert run.status.value == "skipped" and "suppressed" in run.reason
    assert world.uow.delivery_attempts.items == {}
