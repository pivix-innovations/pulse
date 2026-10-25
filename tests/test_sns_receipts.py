from __future__ import annotations

import base64
import datetime
import json

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import NameOID

from providers import sns
from providers.receipts import ReceiptAuthError, ReceiptEvent, ReceiptParseError, parse_receipt

TOPIC = "arn:aws:sns:us-east-1:123456789012:pulse-ses"
CERT_URL = "https://sns.us-east-1.amazonaws.com/SimpleNotificationService-abc.pem"
CONFIG = {"sns_topic_arn": TOPIC}


@pytest.fixture(scope="module")
def keypair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "sns.amazonaws.com")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(1).not_valid_before(now).not_valid_after(now + datetime.timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    return key, cert.public_bytes(serialization.Encoding.PEM)


@pytest.fixture(autouse=True)
def network(keypair, monkeypatch):
    sns._cert_cache.clear()
    fetched: list[str] = []

    async def fake_fetch(url: str) -> bytes:
        fetched.append(url)
        return keypair[1]

    monkeypatch.setattr(sns, "fetch", fake_fetch)
    return fetched


def signed(key, payload: dict, *, kind="Notification", topic=TOPIC, cert_url=CERT_URL, version="2"):
    msg = {
        "Type": kind, "MessageId": "m-1", "TopicArn": topic,
        "Timestamp": "2026-10-13T10:00:00.000Z", "SignatureVersion": version,
        "SigningCertURL": cert_url,
    }
    if kind == "Notification":
        msg["Message"] = json.dumps(payload)
    else:
        msg.update(Message="confirm", Token="tok", SubscribeURL="https://sns.us-east-1.amazonaws.com/?Action=ConfirmSubscription&Token=tok")
    algo = hashes.SHA256() if version == "2" else hashes.SHA1()
    msg["Signature"] = base64.b64encode(
        key.sign(sns.string_to_sign(msg), padding.PKCS1v15(), algo)
    ).decode()
    return json.dumps(msg).encode()


async def receive(body: bytes, **kw):
    return await parse_receipt(
        "email/ses", url=None, headers={}, body=body,
        config=kw.get("config", CONFIG), secrets={}, options=kw.get("options"),
    )


def ses_event(kind, **extra):
    return {"eventType": kind, "mail": {"messageId": "ses-42"}, **extra}


async def test_delivery_event(keypair):
    out = await receive(signed(keypair[0], ses_event("Delivery")))
    assert out == [ReceiptEvent("ses-42", True)]


async def test_permanent_bounce_fails_and_carries_the_diagnostic(keypair):
    event = ses_event("Bounce", bounce={
        "bounceType": "Permanent", "bouncedRecipients": [{"diagnosticCode": "550 no such user"}],
    })
    [e] = await receive(signed(keypair[0], event))
    assert e.delivered is False and "550 no such user" in e.reason


async def test_permanent_bounce_and_complaint_suppress_the_address(keypair):
    bounce = ses_event("Bounce", bounce={"bounceType": "Permanent"})
    [b] = await receive(signed(keypair[0], bounce))
    assert b.suppress == "bounce"
    # A complaint arrives after delivery, so it confirms delivery and flags the address.
    [c] = await receive(signed(keypair[0], ses_event("Complaint")))
    assert c.delivered is True and c.suppress == "complaint"


async def test_transient_bounce_and_send_events_are_not_final(keypair):
    for event in (
        ses_event("Bounce", bounce={"bounceType": "Transient"}),
        ses_event("Send"), ses_event("DeliveryDelay"),
    ):
        assert await receive(signed(keypair[0], event)) == []


async def test_identity_notification_vocabulary_is_understood(keypair):
    event = {"notificationType": "Delivery", "mail": {"messageId": "ses-7"}}
    assert await receive(signed(keypair[0], event)) == [ReceiptEvent("ses-7", True)]


async def test_sha1_signature_version_1(keypair):
    assert await receive(signed(keypair[0], ses_event("Delivery"), version="1"))


async def test_message_from_another_topic_is_rejected(keypair):
    body = signed(keypair[0], ses_event("Delivery"), topic="arn:aws:sns:us-east-1:999:someone-else")
    with pytest.raises(ReceiptAuthError, match="different SNS topic"):
        await receive(body)


async def test_forged_signature_is_rejected(keypair):
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(ReceiptAuthError):
        await receive(signed(other, ses_event("Delivery")))


async def test_tampered_message_is_rejected(keypair):
    tampered = json.loads(signed(keypair[0], ses_event("Bounce", bounce={"bounceType": "Transient"})))
    tampered["Message"] = json.dumps(ses_event("Delivery"))
    with pytest.raises(ReceiptAuthError):
        await receive(json.dumps(tampered).encode())


async def test_certificate_must_come_from_an_sns_host(keypair, network):
    body = signed(keypair[0], ses_event("Delivery"), cert_url="https://evil.example.com/cert.pem")
    with pytest.raises(ReceiptAuthError, match="SNS host"):
        await receive(body)
    assert network == []  # never even fetched


async def test_plain_http_cert_url_is_refused_unless_testing_locally(keypair):
    local = "http://localhost:4566/_aws/sns/cert.pem"
    body = signed(keypair[0], ses_event("Delivery"), cert_url=local)
    with pytest.raises(ReceiptAuthError):
        await receive(body)
    assert await receive(body, options={"allow_insecure_sns": True})


async def test_subscription_confirmation_is_verified_then_confirmed(keypair, network):
    out = await receive(signed(keypair[0], {}, kind="SubscriptionConfirmation"))
    assert out == []
    assert any("ConfirmSubscription" in u for u in network)


async def test_unverified_subscription_confirmation_is_never_followed(keypair, network):
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(ReceiptAuthError):
        await receive(signed(other, {}, kind="SubscriptionConfirmation"))
    assert not any("ConfirmSubscription" in u for u in network)


async def test_no_topic_configured_means_no_receipts(keypair):
    with pytest.raises(ReceiptAuthError, match="No SNS topic"):
        await receive(signed(keypair[0], ses_event("Delivery")), config={})


async def test_garbage_body(keypair):
    with pytest.raises(ReceiptParseError):
        await receive(b"not json")
