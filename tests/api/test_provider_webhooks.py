from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from datetime import datetime, timezone
from urllib.parse import urlencode

import pytest

from config.api import ApiSettings
from config.loader import get_settings
from domain.entities.delivery import DeliveryAttempt
from domain.entities.notification import Notification
from domain.entities.provider import Provider
from domain.enums import ChannelType, DeliveryStatus, NotificationStatus
from domain.value_objects.ids import (
    DeliveryAttemptId, EventId, NotificationId, ProviderId, RecipientId, WorkflowId,
)
from services.secrets import SecretBox, generate_key

PUBLIC = "https://pulse.test"
WHSEC = "whsec_" + base64.b64encode(b"resend-signing-key").decode()


@pytest.fixture
def hooked(world):
    world.app.state.secret_box = SecretBox(generate_key())
    world.app.state.settings = get_settings().model_copy(update={"api": ApiSettings(public_url=PUBLIC)})
    return world


async def make_provider(world, body):
    r = await world.client.post("/api/v1/providers", json=body, headers=world.key_headers)
    assert r.status_code == 201, r.text
    return r.json()


def seed_sent_attempt(world, provider_id, channel, message_id, tenant_id=None):
    tenant = tenant_id or world.tenant_id
    n = Notification(
        id=NotificationId.new(), tenant_id=tenant, event_id=EventId.new(),
        workflow_id=WorkflowId.new(), status=NotificationStatus.PROCESSING,
    )
    world.uow.notifications.items[str(n.id)] = n
    a = DeliveryAttempt(
        id=DeliveryAttemptId.new(), tenant_id=tenant, notification_id=n.id,
        recipient_id=RecipientId.new(), channel=channel,
    )
    a.mark_sent(
        datetime.now(timezone.utc), ProviderId.from_str(provider_id),
        expects_confirmation=True, provider_message_id=message_id,
    )
    world.uow.delivery_attempts.items[str(a.id)] = a
    return a, n


def twilio_call(world, provider, params, token="tok-secret"):
    url = PUBLIC + provider["webhook_path"]
    signed = url + "".join(k + v for k, v in sorted(params.items()))
    sig = base64.b64encode(hmac.new(token.encode(), signed.encode(), hashlib.sha1).digest()).decode()
    return world.client.post(
        provider["webhook_path"], content=urlencode(params),
        headers={"X-Twilio-Signature": sig, "Content-Type": "application/x-www-form-urlencoded"},
    )


TWILIO = {
    "channel": "sms", "name": "twilio",
    "config": {"account_sid": "AC1", "from_number": "+15550001111"},
    "secrets": {"auth_token": "tok-secret"},
}


async def test_twilio_delivered_receipt_confirms_the_attempt_and_settles(hooked):
    provider = await make_provider(hooked, TWILIO)
    assert provider["webhook_url"] == PUBLIC + provider["webhook_path"]
    attempt, notification = seed_sent_attempt(hooked, provider["id"], ChannelType.SMS, "SM900")

    r = await twilio_call(hooked, provider, {"MessageSid": "SM900", "MessageStatus": "delivered"})
    assert r.status_code == 200 and r.json() == {"processed": 1}
    assert attempt.status is DeliveryStatus.DELIVERED
    assert notification.status is NotificationStatus.COMPLETED


async def test_replayed_receipt_is_idempotent(hooked):
    provider = await make_provider(hooked, TWILIO)
    seed_sent_attempt(hooked, provider["id"], ChannelType.SMS, "SM1")
    params = {"MessageSid": "SM1", "MessageStatus": "delivered"}
    assert (await twilio_call(hooked, provider, params)).json() == {"processed": 1}
    assert (await twilio_call(hooked, provider, params)).json() == {"processed": 1}


async def test_undelivered_receipt_fails_the_attempt_for_channel_fallback(hooked):
    provider = await make_provider(hooked, TWILIO)
    attempt, _ = seed_sent_attempt(hooked, provider["id"], ChannelType.SMS, "SM2")
    await twilio_call(
        hooked, provider, {"MessageSid": "SM2", "MessageStatus": "undelivered", "ErrorCode": "30005"}
    )
    assert attempt.status is DeliveryStatus.FAILED and "30005" in attempt.last_error


async def test_forged_receipt_is_rejected_and_changes_nothing(hooked):
    provider = await make_provider(hooked, TWILIO)
    attempt, _ = seed_sent_attempt(hooked, provider["id"], ChannelType.SMS, "SM3")
    r = await twilio_call(
        hooked, provider, {"MessageSid": "SM3", "MessageStatus": "delivered"}, token="attacker"
    )
    assert r.status_code == 401 and attempt.status is DeliveryStatus.SENT


async def test_unknown_message_ids_are_acknowledged_not_errors(hooked):
    provider = await make_provider(hooked, TWILIO)
    r = await twilio_call(hooked, provider, {"MessageSid": "SM-other-app", "MessageStatus": "delivered"})
    assert r.status_code == 200 and r.json() == {"processed": 0}


async def test_unknown_provider_and_unsupported_provider_look_identical(hooked):
    assert (await hooked.client.post(f"/api/v1/webhooks/p/{ProviderId.new()}", content=b"{}")).status_code == 404
    assert (await hooked.client.post("/api/v1/webhooks/p/not-a-uuid", content=b"{}")).status_code == 404
    console = await make_provider(hooked, {"channel": "sms", "name": "console"})
    assert console["webhook_path"] is None
    assert (await hooked.client.post(f"/api/v1/webhooks/p/{console['id']}", content=b"{}")).status_code == 404


async def test_a_receipt_cannot_touch_another_providers_attempts(hooked):
    mine = await make_provider(hooked, TWILIO)
    other = await make_provider(hooked, {**TWILIO, "label": "Other Twilio"})
    attempt, _ = seed_sent_attempt(hooked, other["id"], ChannelType.SMS, "SM-shared")
    # Same sid, but delivered to *my* provider's webhook: must not match.
    r = await twilio_call(hooked, mine, {"MessageSid": "SM-shared", "MessageStatus": "delivered"})
    assert r.json() == {"processed": 0} and attempt.status is DeliveryStatus.SENT


async def test_resend_webhook_end_to_end(hooked):
    provider = await make_provider(hooked, {
        "channel": "email", "name": "resend", "config": {"from_address": "a@x.io"},
        "secrets": {"api_key": "re_k", "webhook_secret": WHSEC},
    })
    attempt, _ = seed_sent_attempt(hooked, provider["id"], ChannelType.EMAIL, "em_77")
    body = json.dumps({"type": "email.delivered", "data": {"email_id": "em_77"}}).encode()
    ts = str(int(time.time()))
    key = base64.b64decode(WHSEC.split("_", 1)[1])
    sig = base64.b64encode(hmac.new(key, b"msg_9." + ts.encode() + b"." + body, hashlib.sha256).digest()).decode()
    r = await hooked.client.post(
        provider["webhook_path"], content=body,
        headers={"svix-id": "msg_9", "svix-timestamp": ts, "svix-signature": f"v1,{sig}"},
    )
    assert r.status_code == 200 and attempt.status is DeliveryStatus.DELIVERED


async def test_delivery_confirmation_is_only_expected_when_receipts_are_set_up(hooked):
    """Without a signing secret the provider can't confirm, so `sent` must stay final."""
    from providers.registry import ProviderRegistry

    reg = ProviderRegistry(hooked.app.state.settings, secret_box=hooked.app.state.secret_box)
    box = hooked.app.state.secret_box

    def row(secrets):
        return Provider(
            id=ProviderId.new(), tenant_id=hooked.tenant_id, channel=ChannelType.EMAIL, name="resend",
            label="r", config={"from_address": "a@x.io"}, secrets_encrypted=box.encrypt(secrets),
        )

    assert reg._build(row({"api_key": "k"})).confirms_delivery is False
    assert reg._build(row({"api_key": "k", "webhook_secret": WHSEC})).confirms_delivery is True


async def test_twilio_requests_a_status_callback_only_when_pulse_is_public(hooked):
    from providers.registry import ProviderRegistry

    box = hooked.app.state.secret_box
    row = Provider(
        id=ProviderId.new(), tenant_id=hooked.tenant_id, channel=ChannelType.SMS, name="twilio",
        label="t", config={"account_sid": "AC", "from_number": "+1"},
        secrets_encrypted=box.encrypt({"auth_token": "t"}),
    )
    public = ProviderRegistry(hooked.app.state.settings, secret_box=box)._build(row)
    assert public.confirms_delivery is True
    assert public._status_callback == PUBLIC + f"/api/v1/webhooks/p/{row.id}"

    private = ProviderRegistry(get_settings(), secret_box=box)._build(row)
    assert private.confirms_delivery is False and private._status_callback is None
