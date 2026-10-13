from __future__ import annotations

import httpx

from providers.email.base import EmailProvider
from providers.http_common import error_detail, make_client, raise_for_status, transport_error
from services.ports.providers import OutboundMessage, SendResult


class SendGridEmailProvider(EmailProvider):
    name = "sendgrid"

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
        content = [{"type": "text/plain", "value": message.body_text or " "}]
        if message.body_html:
            content.append({"type": "text/html", "value": message.body_html})
        payload = {
            "personalizations": [{"to": [{"email": message.to}]}],
            "from": {"email": self._from},
            "subject": message.subject or "(no subject)",
            "content": content,
        }
        if message.idempotency_key:
            payload["custom_args"] = {"pulse_idempotency_key": message.idempotency_key}
        try:
            async with make_client(self._transport) as client:
                response = await client.post(
                    "https://api.sendgrid.com/v3/mail/send",
                    json=payload,
                    headers={"Authorization": f"Bearer {self._key}"},
                )
        except httpx.HTTPError as exc:
            raise transport_error("SendGrid", exc) from exc
        if response.status_code >= 400:
            detail, _ = error_detail(response)
            raise_for_status(response, provider="SendGrid", detail=detail)
        return SendResult(provider_message_id=response.headers.get("X-Message-Id"))


async def check_sendgrid(api_key: str, transport=None) -> tuple[bool, str]:
    try:
        async with make_client(transport) as client:
            r = await client.get(
                "https://api.sendgrid.com/v3/scopes", headers={"Authorization": f"Bearer {api_key}"}
            )
    except httpx.HTTPError as exc:
        return False, f"Could not reach SendGrid: {exc!r}"
    if r.status_code == 200:
        return True, "SendGrid API key is valid"
    return False, "SendGrid rejected the API key" if r.status_code in (401, 403) else f"SendGrid returned {r.status_code}"
