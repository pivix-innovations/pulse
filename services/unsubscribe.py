"""Signed unsubscribe links: the token names the tenant, channel and address, so the public endpoint needs no login."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json

from domain.entities.suppression import normalize_address
from domain.enums import ChannelType
from domain.value_objects.ids import InvalidIdError, TenantId

PATH = "/api/v1/unsubscribe"


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


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

    def token(self, tenant_id: TenantId, channel: ChannelType, address: str) -> str:
        payload = {"t": str(tenant_id), "c": channel.value, "a": normalize_address(channel, address)}
        body = _b64(json.dumps(payload, separators=(",", ":")).encode())
        return f"{body}.{self._sign(body)}"

    def url_for(self, tenant_id: TenantId, channel: ChannelType, address: str | None) -> str | None:
        if not self.enabled or not address:
            return None
        return f"{self._public_url}{PATH}?token={self.token(tenant_id, channel, address)}"

    def verify(self, token: str) -> tuple[TenantId, ChannelType, str] | None:
        if not self.enabled:
            return None
        try:
            body, sig = token.split(".", 1)
            if not hmac.compare_digest(sig, self._sign(body)):
                return None
            data = json.loads(_unb64(body))
            return TenantId.from_str(data["t"]), ChannelType(data["c"]), str(data["a"])
        except (ValueError, KeyError, TypeError, InvalidIdError):
            return None
