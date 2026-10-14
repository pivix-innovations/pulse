"""
Signed unsubscribe links: the token names the tenant, channel and address (and, for categorised messages, the recipient
and category), so the public endpoint needs no login. Address links suppress the address; category links only turn
off that category for that recipient.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass

from domain.entities.suppression import normalize_address
from domain.enums import ChannelType
from domain.value_objects.ids import InvalidIdError, TenantId

PATH = "/api/v1/unsubscribe"


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


@dataclass(frozen=True, slots=True)
class UnsubscribeTarget:
    tenant_id: TenantId
    channel: ChannelType
    address: str
    category: str | None = None        # set: opt the recipient out of this category instead of suppressing the address
    recipient_ref: str | None = None


class UnsubscribeLinks:
    """Builds and verifies links. Disabled (no links, nothing verifies) until a key and a public URL are set."""

    def __init__(self, key: str, public_url: str | None) -> None:
        self._key = key.encode()
        self._public_url = (public_url or "").rstrip("/")

    @property
    def enabled(self) -> bool:
        return bool(self._key and self._public_url)

    def _sign(self, body: str) -> str:
        return _b64(hmac.new(self._key, body.encode(), hashlib.sha256).digest())

    def token(
        self, tenant_id: TenantId, channel: ChannelType, address: str,
        *, category: str | None = None, recipient_ref: str | None = None,
    ) -> str:
        payload = {"t": str(tenant_id), "c": channel.value, "a": normalize_address(channel, address)}
        if category and recipient_ref:
            payload.update(k=category, r=recipient_ref)
        body = _b64(json.dumps(payload, separators=(",", ":")).encode())
        return f"{body}.{self._sign(body)}"

    def url_for(
        self, tenant_id: TenantId, channel: ChannelType, address: str | None,
        *, category: str | None = None, recipient_ref: str | None = None,
    ) -> str | None:
        if not self.enabled or not address:
            return None
        token = self.token(tenant_id, channel, address, category=category, recipient_ref=recipient_ref)
        return f"{self._public_url}{PATH}?token={token}"

    def verify(self, token: str) -> UnsubscribeTarget | None:
        if not self.enabled:
            return None
        try:
            body, sig = token.split(".", 1)
            if not hmac.compare_digest(sig, self._sign(body)):
                return None
            data = json.loads(_unb64(body))
            return UnsubscribeTarget(
                TenantId.from_str(data["t"]), ChannelType(data["c"]), str(data["a"]),
                category=data.get("k"), recipient_ref=data.get("r"),
            )
        except (ValueError, KeyError, TypeError, InvalidIdError):
            return None
