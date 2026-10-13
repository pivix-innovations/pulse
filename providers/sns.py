"""
Amazon SNS message verification, used to trust SES delivery events.

SNS signs every message it POSTs to an HTTPS endpoint. To trust one we fetch
the signing certificate (only from an AWS SNS host), rebuild the canonical
string-to-sign and check the RSA signature. We also pin the message to the
topic the tenant configured, so a valid SNS message from someone else's topic
is rejected.
"""
from __future__ import annotations

import base64
import re
from typing import Any
from urllib.parse import urlparse

import httpx
from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding

_SNS_HOST = re.compile(r"^sns\.[a-z0-9-]+\.amazonaws\.com(\.cn)?$")
_AWS_HOST = re.compile(r"^[a-z0-9.-]+\.amazonaws\.com(\.cn)?$")
# Local emulators only; enabled explicitly by PULSE_WEBHOOK__ALLOW_INSECURE_SNS.
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "localhost.localstack.cloud"}

_NOTIFICATION_KEYS = ("Message", "MessageId", "Subject", "Timestamp", "TopicArn", "Type")
_CONFIRMATION_KEYS = ("Message", "MessageId", "SubscribeURL", "Timestamp", "Token", "TopicArn", "Type")

_cert_cache: dict[str, x509.Certificate] = {}


class SnsVerificationError(Exception):
    pass


def url_allowed(url: str, *, pattern: re.Pattern[str], allow_insecure: bool) -> bool:
    parsed = urlparse(url)
    host = parsed.hostname or ""
    if allow_insecure and host in _LOCAL_HOSTS and parsed.scheme in ("http", "https"):
        return True
    return parsed.scheme == "https" and bool(pattern.match(host))


async def fetch(url: str) -> bytes:
    """Network seam: tests replace this."""
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.content


async def _certificate(url: str) -> x509.Certificate:
    cert = _cert_cache.get(url)
    if cert is None:
        cert = x509.load_pem_x509_certificate(await fetch(url))
        _cert_cache[url] = cert
    return cert


def string_to_sign(message: dict[str, Any]) -> bytes:
    kind = message.get("Type")
    if kind == "Notification":
        keys = _NOTIFICATION_KEYS
    elif kind in ("SubscriptionConfirmation", "UnsubscribeConfirmation"):
        keys = _CONFIRMATION_KEYS
    else:
        raise SnsVerificationError(f"Unknown SNS message type {kind!r}")
    return "".join(f"{k}\n{message[k]}\n" for k in keys if k in message).encode()


async def verify_message(message: dict[str, Any], *, allow_insecure: bool = False) -> None:
    """Raises SnsVerificationError unless the message is a genuine SNS signature."""
    cert_url = message.get("SigningCertURL") or message.get("SigningCertUrl") or ""
    if not url_allowed(cert_url, pattern=_SNS_HOST, allow_insecure=allow_insecure):
        raise SnsVerificationError("Signing certificate is not from an SNS host")
    try:
        algorithm = {"1": hashes.SHA1(), "2": hashes.SHA256()}[str(message.get("SignatureVersion"))]
        signature = base64.b64decode(message["Signature"])
        cert = await _certificate(cert_url)
        cert.public_key().verify(signature, string_to_sign(message), padding.PKCS1v15(), algorithm)
    except SnsVerificationError:
        raise
    except (KeyError, ValueError, InvalidSignature, httpx.HTTPError, TypeError) as exc:
        raise SnsVerificationError(f"SNS signature check failed: {exc!r}") from exc


async def confirm_subscription(message: dict[str, Any], *, allow_insecure: bool = False) -> None:
    url = message.get("SubscribeURL", "")
    if not url_allowed(url, pattern=_AWS_HOST, allow_insecure=allow_insecure):
        raise SnsVerificationError("SubscribeURL is not an AWS host")
    await fetch(url)
