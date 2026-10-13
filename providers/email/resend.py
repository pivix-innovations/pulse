from __future__ import annotations

import httpx

from providers.email.base import EmailProvider
from providers.http_common import error_detail, make_client, raise_for_status, transport_error
from services.ports.providers import OutboundMessage, SendResult


class ResendEmailProvider(EmailProvider):
    name = "resend"

    def __init__(
        self,
        *,
        api_key: str,
        from_address: str,
        receipts: bool = False,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        # True once the tenant has set up the provider's webhook, so receipts
        # can confirm delivery; otherwise `sent` is final.
        self.confirms_delivery = receipts
        self._key = api_key
        self._from = from_address
        self._transport = transport

    async def send(self, message: OutboundMessage) -> SendResult:
        payload = {
            "from": self._from,
            "to": [message.to],
            "subject": message.subject or "",
            "text": message.body_text,
        }
        if message.body_html:
            payload["html"] = message.body_html
        headers = {"Authorization": f"Bearer {self._key}"}
        if message.idempotency_key:
            headers["Idempotency-Key"] = message.idempotency_key
        try:
            async with make_client(self._transport) as client:
                response = await client.post(
                    "https://api.resend.com/emails", json=payload, headers=headers
                )
        except httpx.HTTPError as exc:
            raise transport_error("Resend", exc) from exc
        if response.status_code >= 400:
            detail, _ = error_detail(response)
            raise_for_status(response, provider="Resend", detail=detail)
        return SendResult(provider_message_id=response.json().get("id"))


async def check_resend(api_key: str, transport=None) -> tuple[bool, str]:
    try:
        async with make_client(transport) as client:
            r = await client.get(
                "https://api.resend.com/domains", headers={"Authorization": f"Bearer {api_key}"}
            )
    except httpx.HTTPError as exc:
        return False, f"Could not reach Resend: {exc!r}"
    if r.status_code == 200:
        return True, "Resend API key is valid"
    if r.status_code == 401 and "restricted" in r.text.lower():
        return True, "Key is valid (send-only keys cannot list domains)"
    return False, "Resend rejected the API key" if r.status_code in (401, 403) else f"Resend returned {r.status_code}"
