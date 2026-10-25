from __future__ import annotations

import pytest

from config.api import ApiSettings
from config.loader import get_settings
from domain.entities.recipient import Recipient
from domain.enums import ChannelType, DeliveryStatus
from domain.value_objects.ids import RecipientId
from tests.api.test_events_notifications import EVENT
from services.secrets import SecretBox, generate_key
from tests.api.test_provider_webhooks import PUBLIC, TWILIO, make_provider, seed_sent_attempt, twilio_call


@pytest.fixture
def hooked(world):
    world.app.state.secret_box = SecretBox(generate_key())
    world.app.state.settings = get_settings().model_copy(update={"api": ApiSettings(public_url=PUBLIC)})
    return world


async def test_manual_suppression_crud_normalises_and_rejects_duplicates(world):
    h = world.key_headers
    r = await world.client.post(
        "/api/v1/suppressions", json={"channel": "email", "address": " Ada@Example.COM "}, headers=h
    )
    assert r.status_code == 201 and r.json()["address"] == "ada@example.com" and r.json()["reason"] == "manual"
    dup = await world.client.post(
        "/api/v1/suppressions", json={"channel": "email", "address": "ada@example.com"}, headers=h
    )
    assert dup.status_code == 409
    push = await world.client.post("/api/v1/suppressions", json={"channel": "push", "address": "abc"}, headers=h)
    assert push.status_code == 422

    listing = (await world.client.get("/api/v1/suppressions", headers=h)).json()
    assert [s["address"] for s in listing] == ["ada@example.com"]

    sid = listing[0]["id"]
    assert (await world.client.delete(f"/api/v1/suppressions/{sid}", headers=h)).status_code == 204
    assert (await world.client.delete(f"/api/v1/suppressions/{sid}", headers=h)).status_code == 404
    assert (await world.client.get("/api/v1/suppressions", headers=h)).json() == []


async def test_suppressed_email_address_is_skipped_but_other_channels_still_send(world):
    h = world.key_headers
    await world.client.post(
        "/api/v1/suppressions", json={"channel": "email", "address": "U1@example.com"}, headers=h
    )
    nid = (await world.client.post("/api/v1/events", json=EVENT, headers=h)).json()["notification_id"]
    detail = (await world.client.get(f"/api/v1/notifications/{nid}", headers=h)).json()
    assert {a["channel"] for a in detail["attempts"]} == {"in_app", "push"}


async def test_removing_a_suppression_resumes_sending(world):
    h = world.key_headers
    sid = (await world.client.post(
        "/api/v1/suppressions", json={"channel": "email", "address": "u1@example.com"}, headers=h
    )).json()["id"]
    await world.client.delete(f"/api/v1/suppressions/{sid}", headers=h)
    nid = (await world.client.post("/api/v1/events", json=EVENT, headers=h)).json()["notification_id"]
    detail = (await world.client.get(f"/api/v1/notifications/{nid}", headers=h)).json()
    assert "email" in {a["channel"] for a in detail["attempts"]}


async def test_twilio_stop_reply_suppresses_the_phone_number(hooked):
    world = hooked
    provider = await make_provider(world, TWILIO)
    recipient = Recipient(
        id=RecipientId.new(), tenant_id=world.tenant_id, external_ref="u9", phone="+1 555 000 2222"
    )
    world.uow.recipients.items[str(recipient.id)] = recipient
    attempt, _ = seed_sent_attempt(world, provider["id"], ChannelType.SMS, "SM77")
    attempt.recipient_id = recipient.id

    r = await twilio_call(world, provider, {
        "MessageSid": "SM77", "MessageStatus": "undelivered", "ErrorCode": "21610",
    })
    assert r.status_code == 200
    assert attempt.status is DeliveryStatus.FAILED
    assert await world.uow.suppressions.is_suppressed(world.tenant_id, ChannelType.SMS, "+15550002222")
    listed = (await world.client.get("/api/v1/suppressions", headers=world.key_headers)).json()
    assert listed[0]["reason"] == "unsubscribe" and listed[0]["address"] == "+15550002222"


async def test_ordinary_failure_does_not_suppress(hooked):
    world = hooked
    provider = await make_provider(world, TWILIO)
    attempt, _ = seed_sent_attempt(world, provider["id"], ChannelType.SMS, "SM78")
    await twilio_call(world, provider, {"MessageSid": "SM78", "MessageStatus": "failed", "ErrorCode": "30003"})
    assert world.uow.suppressions.items == []
