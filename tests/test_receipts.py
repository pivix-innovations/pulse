from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from providers.receipts import (
    ReceiptAuthError,
    ReceiptEvent,
    ReceiptParseError,
    parse_resend,
    parse_sendgrid,
    parse_twilio,
    webhook_url,
)

URL = "https://pulse.test/api/v1/webhooks/p/abc"


# ── Twilio ─────────────────────────────────────────────────────────────


def twilio_request(params: dict, token="tok", url=URL):
    body = urlencode(params).encode()
    signed = url + "".join(k + v for k, v in sorted(params.items()))
    sig = base64.b64encode(hmac.new(token.encode(), signed.encode(), hashlib.sha1).digest()).decode()
    return dict(url=url, headers={"x-twilio-signature": sig}, body=body, config={}, secrets={"auth_token": "tok"})


def test_twilio_delivered_and_failed_statuses():
    ok = parse_twilio(**twilio_request({"MessageSid": "SM1", "MessageStatus": "delivered"}))
    assert ok == [ReceiptEvent("SM1", True)]
    bad = parse_twilio(
        **twilio_request({"MessageSid": "SM2", "MessageStatus": "undelivered", "ErrorCode": "30003"})
    )
    assert bad[0].delivered is False and "30003" in bad[0].reason


def test_twilio_ignores_non_final_statuses():
    assert parse_twilio(**twilio_request({"MessageSid": "SM1", "MessageStatus": "sent"})) == []


def test_twilio_rejects_a_forged_or_tampered_callback():
    req = twilio_request({"MessageSid": "SM1", "MessageStatus": "delivered"}, token="WRONG")
    with pytest.raises(ReceiptAuthError):
        parse_twilio(**req)
    req = twilio_request({"MessageSid": "SM1", "MessageStatus": "delivered"})
    req["body"] = urlencode({"MessageSid": "SM1", "MessageStatus": "undelivered"}).encode()
    with pytest.raises(ReceiptAuthError):
        parse_twilio(**req)


def test_twilio_requires_a_public_url_to_verify():
    req = twilio_request({"MessageSid": "SM1", "MessageStatus": "delivered"})
    req["url"] = None
    with pytest.raises(ReceiptAuthError):
        parse_twilio(**req)


# ── Resend / Svix ──────────────────────────────────────────────────────

SECRET = "whsec_" + base64.b64encode(b"super-secret-key").decode()


def resend_request(event: dict, *, ts=None, secret=SECRET):
    body = json.dumps(event).encode()
    ts = str(int(ts if ts is not None else time.time()))
    key = base64.b64decode(secret.split("_", 1)[1])
    sig = base64.b64encode(hmac.new(key, b"msg_1." + ts.encode() + b"." + body, hashlib.sha256).digest()).decode()
    return dict(
        url=None,
        headers={"svix-id": "msg_1", "svix-timestamp": ts, "svix-signature": f"v1,{sig}"},
        body=body, config={}, secrets={"webhook_secret": SECRET},
    )


def test_resend_delivered_bounced_and_ignored_events():
    d = parse_resend(**resend_request({"type": "email.delivered", "data": {"email_id": "e1"}}))
    assert d == [ReceiptEvent("e1", True)]
    b = parse_resend(**resend_request({"type": "email.bounced", "data": {"email_id": "e2"}}))
    assert b[0].delivered is False and "bounced" in b[0].reason
    assert parse_resend(**resend_request({"type": "email.opened", "data": {"email_id": "e3"}})) == []


def test_resend_rejects_bad_signature_and_stale_timestamps():
    with pytest.raises(ReceiptAuthError):
        parse_resend(**resend_request({"type": "email.delivered", "data": {"email_id": "e"}},
                                      secret="whsec_" + base64.b64encode(b"other").decode()))
    with pytest.raises(ReceiptAuthError, match="old"):
        parse_resend(**resend_request({"type": "email.delivered", "data": {"email_id": "e"}},
                                      ts=time.time() - 3600))


def test_resend_authentic_but_malformed_payload():
    with pytest.raises(ReceiptParseError):
        parse_resend(**resend_request({"nope": 1}))


# ── SendGrid ───────────────────────────────────────────────────────────


@pytest.fixture
def sg_key():
    return ec.generate_private_key(ec.SECP256R1())


def sendgrid_request(events, key, ts=None):
    body = json.dumps(events).encode()
    ts = str(int(ts if ts is not None else time.time()))
    sig = base64.b64encode(key.sign(ts.encode() + body, ec.ECDSA(hashes.SHA256()))).decode()
    pub = base64.b64encode(
        key.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
    ).decode()
    return dict(
        url=None,
        headers={
            "x-twilio-email-event-webhook-signature": sig,
            "x-twilio-email-event-webhook-timestamp": ts,
        },
        body=body, config={}, secrets={"webhook_public_key": pub},
    )


def test_sendgrid_batch_maps_each_event_and_strips_the_filter_suffix(sg_key):
    events = [
        {"event": "delivered", "sg_message_id": "abc123.filterdrecv-1"},
        {"event": "bounce", "sg_message_id": "def456.filter9", "reason": "550 no such user"},
        {"event": "open", "sg_message_id": "abc123.filterdrecv-1"},
        {"event": "processed", "sg_message_id": "zzz.filter"},
    ]
    out = parse_sendgrid(**sendgrid_request(events, sg_key))
    assert out[0] == ReceiptEvent("abc123", True)
    assert out[1].provider_message_id == "def456" and out[1].delivered is False
    assert "550 no such user" in out[1].reason
    assert len(out) == 2


def test_sendgrid_rejects_wrong_key_and_tampered_body(sg_key):
    other = ec.generate_private_key(ec.SECP256R1())
    req = sendgrid_request([{"event": "delivered", "sg_message_id": "a.b"}], sg_key)
    req["secrets"] = sendgrid_request([], other)["secrets"]
    with pytest.raises(ReceiptAuthError):
        parse_sendgrid(**req)
    req = sendgrid_request([{"event": "delivered", "sg_message_id": "a.b"}], sg_key)
    req["body"] = req["body"].replace(b"delivered", b"bounce.d.")
    with pytest.raises(ReceiptAuthError):
        parse_sendgrid(**req)


def test_webhook_url_helpers():
    assert webhook_url("https://x.io/", "p1") == "https://x.io/api/v1/webhooks/p/p1"
    assert webhook_url(None, "p1") is None
