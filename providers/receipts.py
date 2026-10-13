"""
Delivery receipts: verifying and parsing the callbacks providers send once a
message is delivered or bounces. Each saved provider has its own webhook URL
(see `webhook_path`), so the secrets used to verify a callback are that
provider's own, never a global one.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import inspect
import json
import time
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import parse_qsl

from domain.value_objects.ids import ProviderId

TOLERANCE_SECONDS = 600


class ReceiptAuthError(Exception):
    """The callback's signature is missing or wrong."""


class ReceiptParseError(Exception):
    """The callback is authentic but not in the expected shape."""


@dataclass(frozen=True, slots=True)
class ReceiptEvent:
    provider_message_id: str
    delivered: bool  # False -> bounced / undelivered / failed
    reason: str | None = None


def webhook_path(provider_id: ProviderId | str) -> str:
    return f"/api/v1/webhooks/p/{provider_id}"


def webhook_url(public_url: str | None, provider_id: ProviderId | str) -> str | None:
    if not public_url:
        return None
    return public_url.rstrip("/") + webhook_path(provider_id)


def _fresh(timestamp: str) -> None:
    try:
        age = abs(time.time() - float(timestamp))
    except ValueError:
        raise ReceiptAuthError("Malformed timestamp")
    if age > TOLERANCE_SECONDS:
        raise ReceiptAuthError("Callback timestamp is too old")


# ── Twilio ─────────────────────────────────────────────────────────────

_TWILIO_FAILED = {"undelivered", "failed", "canceled"}


def parse_twilio(
    *, url: str | None, headers: dict[str, str], body: bytes, config: dict, secrets: dict, **_: Any
) -> list[ReceiptEvent]:
    token = secrets.get("auth_token")
    signature = headers.get("x-twilio-signature")
    if not token or not signature or not url:
        raise ReceiptAuthError("Twilio signature or webhook URL missing")
    params = parse_qsl(body.decode(), keep_blank_values=True)
    signed = url + "".join(k + v for k, v in sorted(params))
    expected = base64.b64encode(
        hmac.new(token.encode(), signed.encode(), hashlib.sha1).digest()
    ).decode()
    if not hmac.compare_digest(expected, signature):
        raise ReceiptAuthError("Invalid Twilio signature")

    data = dict(params)
    sid, status = data.get("MessageSid"), (data.get("MessageStatus") or "").lower()
    if not sid:
        raise ReceiptParseError("Missing MessageSid")
    if status == "delivered":
        return [ReceiptEvent(sid, True)]
    if status in _TWILIO_FAILED:
        code = data.get("ErrorCode")
        return [ReceiptEvent(sid, False, f"Twilio {status}" + (f" (error {code})" if code else ""))]
    return []  # queued / sending / sent: not final


# ── Resend (Svix-signed) ───────────────────────────────────────────────

_RESEND_FAILED = {"email.bounced": "bounced", "email.failed": "failed", "email.suppressed": "suppressed"}


def parse_resend(
    *, url: str | None, headers: dict[str, str], body: bytes, config: dict, secrets: dict, **_: Any
) -> list[ReceiptEvent]:
    secret = secrets.get("webhook_secret")
    msg_id, ts, sig_header = (
        headers.get("svix-id"), headers.get("svix-timestamp"), headers.get("svix-signature"),
    )
    if not secret or not (msg_id and ts and sig_header):
        raise ReceiptAuthError("Resend signature headers or signing secret missing")
    _fresh(ts)
    raw = secret.split("_", 1)[1] if secret.startswith("whsec_") else secret
    try:
        key = base64.b64decode(raw)
    except ValueError:
        key = raw.encode()
    expected = base64.b64encode(
        hmac.new(key, f"{msg_id}.{ts}.".encode() + body, hashlib.sha256).digest()
    ).decode()
    candidates = [part.split(",", 1)[1] for part in sig_header.split() if "," in part]
    if not any(hmac.compare_digest(expected, c) for c in candidates):
        raise ReceiptAuthError("Invalid Resend signature")

    try:
        event = json.loads(body)
        kind, email_id = event["type"], event["data"]["email_id"]
    except (ValueError, KeyError, TypeError):
        raise ReceiptParseError("Unexpected Resend payload")
    if kind == "email.delivered":
        return [ReceiptEvent(email_id, True)]
    if kind in _RESEND_FAILED:
        return [ReceiptEvent(email_id, False, f"Resend {_RESEND_FAILED[kind]}")]
    return []  # sent / delivery_delayed / opened / clicked ...


# ── SendGrid Event Webhook (ECDSA-signed) ──────────────────────────────

_SENDGRID_FAILED = {"bounce": "bounced", "dropped": "dropped"}


def parse_sendgrid(
    *, url: str | None, headers: dict[str, str], body: bytes, config: dict, secrets: dict, **_: Any
) -> list[ReceiptEvent]:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    public_key = secrets.get("webhook_public_key")
    sig = headers.get("x-twilio-email-event-webhook-signature")
    ts = headers.get("x-twilio-email-event-webhook-timestamp")
    if not public_key or not sig or not ts:
        raise ReceiptAuthError("SendGrid signature headers or public key missing")
    _fresh(ts)
    try:
        key = serialization.load_der_public_key(base64.b64decode(public_key))
        key.verify(base64.b64decode(sig), ts.encode() + body, ec.ECDSA(hashes.SHA256()))
    except (InvalidSignature, ValueError, TypeError):
        raise ReceiptAuthError("Invalid SendGrid signature")

    try:
        events = json.loads(body)
        assert isinstance(events, list)
    except (ValueError, AssertionError):
        raise ReceiptParseError("SendGrid events must be a JSON array")
    out: list[ReceiptEvent] = []
    for e in events:
        if not isinstance(e, dict) or not e.get("sg_message_id"):
            continue
        # sg_message_id is "<X-Message-Id>.<filter suffix>"
        mid = str(e["sg_message_id"]).split(".", 1)[0]
        kind = e.get("event")
        if kind == "delivered":
            out.append(ReceiptEvent(mid, True))
        elif kind in _SENDGRID_FAILED:
            reason = e.get("reason") or _SENDGRID_FAILED[kind]
            out.append(ReceiptEvent(mid, False, f"SendGrid {_SENDGRID_FAILED[kind]}: {reason}"[:300]))
    return out


# ── Amazon SES (via SNS) ───────────────────────────────────────────────


async def parse_ses(
    *,
    url: str | None,
    headers: dict[str, str],
    body: bytes,
    config: dict,
    secrets: dict,
    options: dict | None = None,
    **_: Any,
) -> list[ReceiptEvent]:
    """
    SES publishes events to an SNS topic; SNS POSTs them here. Subscription
    confirmations are verified and then confirmed on the spot.
    """
    from providers import sns

    allow_insecure = bool((options or {}).get("allow_insecure_sns"))
    topic = config.get("sns_topic_arn")
    if not topic:
        raise ReceiptAuthError("No SNS topic is configured for this provider")
    try:
        message = json.loads(body)
        assert isinstance(message, dict)
    except (ValueError, AssertionError):
        raise ReceiptParseError("SNS body must be a JSON object")
    if message.get("TopicArn") != topic:
        raise ReceiptAuthError("Message is from a different SNS topic")
    try:
        await sns.verify_message(message, allow_insecure=allow_insecure)
        kind = message.get("Type")
        if kind == "SubscriptionConfirmation":
            await sns.confirm_subscription(message, allow_insecure=allow_insecure)
            return []
    except sns.SnsVerificationError as exc:
        raise ReceiptAuthError(str(exc))
    if kind != "Notification":
        return []

    try:
        event = json.loads(message["Message"])
        mail_id = event["mail"]["messageId"]
    except (ValueError, KeyError, TypeError):
        raise ReceiptParseError("Unexpected SES event payload")
    # `eventType` (configuration-set events) or `notificationType` (identity notifications)
    etype = event.get("eventType") or event.get("notificationType")
    if etype == "Delivery":
        return [ReceiptEvent(mail_id, True)]
    if etype == "Bounce":
        bounce = event.get("bounce", {})
        if bounce.get("bounceType") == "Permanent":
            detail = (bounce.get("bouncedRecipients") or [{}])[0].get("diagnosticCode", "")
            return [ReceiptEvent(mail_id, False, f"SES permanent bounce: {detail}".strip(": ")[:300])]
        return []  # transient/undetermined: SES keeps trying
    if etype == "Reject":
        return [ReceiptEvent(mail_id, False, f"SES rejected: {event.get('reject', {}).get('reason', '')}"[:300])]
    return []  # Send, Complaint (after delivery), DeliveryDelay, Open, Click ...


Parser = Callable[..., list[ReceiptEvent]]

_PARSERS: dict[str, Parser] = {
    "sms/twilio": parse_twilio,
    "email/resend": parse_resend,
    "email/sendgrid": parse_sendgrid,
    "email/ses": parse_ses,
}


def supports_receipts(spec_id: str) -> bool:
    return spec_id in _PARSERS


async def parse_receipt(
    spec_id: str,
    *,
    url: str | None,
    headers: dict[str, str],
    body: bytes,
    config: dict[str, Any],
    secrets: dict[str, Any],
    options: dict[str, Any] | None = None,
) -> list[ReceiptEvent]:
    parser = _PARSERS.get(spec_id)
    if parser is None:
        raise ReceiptParseError(f"{spec_id} does not send delivery receipts")
    result = parser(
        url=url, headers=headers, body=body, config=config, secrets=secrets, options=options
    )
    return await result if inspect.isawaitable(result) else result
