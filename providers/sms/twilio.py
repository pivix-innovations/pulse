from __future__ import annotations

import httpx

from providers.http_common import error_detail, make_client, raise_for_status, transport_error
from providers.sms.base import SmsProvider
from services.ports.providers import OutboundMessage, SendResult

# Twilio error codes meaning the *number* is unusable (never fail over for these).
_BAD_NUMBER = frozenset({21211, 21214, 21217, 21408, 21610, 21612, 21614})


class TwilioSmsProvider(SmsProvider):
    name = "twilio"
    # True only when Pulse is publicly reachable, so Twilio's status
    # callbacks can confirm delivery; otherwise `sent` is final.

    def __init__(
        self,
        *,
        account_sid: str,
        auth_token: str,
        from_number: str | None = None,
        messaging_service_sid: str | None = None,
        status_callback: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._sid = account_sid
        self._token = auth_token
        self._from = from_number
        self._service = messaging_service_sid
        self._status_callback = status_callback
        self.confirms_delivery = bool(status_callback)
        self._transport = transport

    async def send(self, message: OutboundMessage) -> SendResult:
        data = {"To": message.to, "Body": message.body_text}
        if self._status_callback:
            data["StatusCallback"] = self._status_callback
        if self._service:
            data["MessagingServiceSid"] = self._service
        else:
            data["From"] = self._from or ""
        try:
            async with make_client(self._transport) as client:
                response = await client.post(
                    f"https://api.twilio.com/2010-04-01/Accounts/{self._sid}/Messages.json",
                    data=data,
                    auth=(self._sid, self._token),
                )
        except httpx.HTTPError as exc:
            raise transport_error("Twilio", exc) from exc
        if response.status_code >= 400:
            detail, code = error_detail(response)
            raise_for_status(
                response, provider="Twilio", detail=detail,
                bad_destination_codes=_BAD_NUMBER, code=code,
            )
        return SendResult(provider_message_id=response.json().get("sid"))


async def check_twilio(account_sid: str, auth_token: str, transport=None) -> tuple[bool, str]:
    try:
        async with make_client(transport) as client:
            r = await client.get(
                f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}.json",
                auth=(account_sid, auth_token),
            )
    except httpx.HTTPError as exc:
        return False, f"Could not reach Twilio: {exc!r}"
    if r.status_code == 200:
        status = r.json().get("status", "unknown")
        return (status == "active"), f"Twilio account is {status}"
    return False, "Twilio rejected the Account SID / Auth Token" if r.status_code == 401 else f"Twilio returned {r.status_code}"
