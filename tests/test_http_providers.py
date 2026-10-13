from __future__ import annotations

import json

import httpx
import pytest

from domain.enums import ChannelType
from providers.email.resend import ResendEmailProvider, check_resend
from providers.email.sendgrid import SendGridEmailProvider, check_sendgrid
from providers.sms.twilio import TwilioSmsProvider, check_twilio
from services.ports.providers import OutboundMessage, ProviderSendError


def transport(status=200, body=None, headers=None, capture=None, raises=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if capture is not None:
            capture.append(request)
        if raises:
            raise raises
        return httpx.Response(status, json=body if body is not None else {}, headers=headers)

    return httpx.MockTransport(handler)


def email(**kw):
    return OutboundMessage(
        channel=ChannelType.EMAIL, to="u@x.io", subject="Hi", body_text="Hello",
        body_html="<p>Hello</p>", idempotency_key="n:r:email", **kw,
    )


def sms():
    return OutboundMessage(channel=ChannelType.SMS, to="+15550001111", body_text="Hello")


# ── Twilio ─────────────────────────────────────────────────────────────


def twilio(t, **kw):
    return TwilioSmsProvider(
        account_sid="ACxxx", auth_token="tok", from_number="+15559990000", transport=t, **kw
    )


async def test_twilio_posts_form_with_basic_auth_and_returns_sid():
    seen = []
    result = await twilio(transport(201, {"sid": "SM123"}, capture=seen)).send(sms())
    assert result.provider_message_id == "SM123"
    [req] = seen
    assert str(req.url).endswith("/Accounts/ACxxx/Messages.json")
    assert req.headers["authorization"].startswith("Basic ")
    body = req.content.decode()
    assert "To=%2B15550001111" in body and "From=%2B15559990000" in body


async def test_twilio_messaging_service_replaces_from():
    seen = []
    p = TwilioSmsProvider(
        account_sid="AC", auth_token="t", messaging_service_sid="MG1",
        transport=transport(201, {"sid": "S"}, capture=seen),
    )
    await p.send(sms())
    assert "MessagingServiceSid=MG1" in seen[0].content.decode()
    assert "From=" not in seen[0].content.decode()


@pytest.mark.parametrize("code", [21211, 21610, 21614])
async def test_twilio_bad_number_stops_the_chain(code):
    with pytest.raises(ProviderSendError) as err:
        await twilio(transport(400, {"code": code, "message": "bad number"})).send(sms())
    assert err.value.retryable is False and err.value.failover is False


async def test_twilio_auth_failure_fails_over_without_retry():
    with pytest.raises(ProviderSendError) as err:
        await twilio(transport(401, {"code": 20003, "message": "Authenticate"})).send(sms())
    assert err.value.retryable is False and err.value.failover is True


@pytest.mark.parametrize("status", [429, 500, 503])
async def test_twilio_outage_retries_and_fails_over(status):
    with pytest.raises(ProviderSendError) as err:
        await twilio(transport(status, {"message": "slow down"})).send(sms())
    assert err.value.retryable is True and err.value.failover is True


async def test_network_error_counts_as_provider_unreachable():
    with pytest.raises(ProviderSendError) as err:
        await twilio(transport(raises=httpx.ConnectTimeout("t"))).send(sms())
    assert err.value.retryable is True and err.value.failover is True


async def test_twilio_check():
    assert (await check_twilio("AC", "t", transport(200, {"status": "active"})))[0] is True
    assert (await check_twilio("AC", "t", transport(200, {"status": "suspended"})))[0] is False
    ok, detail = await check_twilio("AC", "t", transport(401, {}))
    assert ok is False and "rejected" in detail


# ── Resend ─────────────────────────────────────────────────────────────


async def test_resend_sends_json_with_idempotency_key():
    seen = []
    p = ResendEmailProvider(
        api_key="re_x", from_address="a@x.io", transport=transport(200, {"id": "em_1"}, capture=seen)
    )
    result = await p.send(email())
    assert result.provider_message_id == "em_1"
    req = seen[0]
    assert req.headers["authorization"] == "Bearer re_x"
    assert req.headers["idempotency-key"] == "n:r:email"
    payload = json.loads(req.content)
    assert payload["to"] == ["u@x.io"] and payload["html"] == "<p>Hello</p>"


async def test_resend_validation_error_does_not_fail_over():
    p = ResendEmailProvider(
        api_key="k", from_address="a@x.io", transport=transport(422, {"message": "invalid to"})
    )
    with pytest.raises(ProviderSendError) as err:
        await p.send(email())
    assert err.value.failover is False and err.value.retryable is False


async def test_resend_check_accepts_send_only_keys():
    t = transport(401, {"name": "restricted_api_key", "message": "restricted"})
    assert (await check_resend("k", t))[0] is True
    assert (await check_resend("k", transport(401, {"message": "invalid"})))[0] is False


# ── SendGrid ───────────────────────────────────────────────────────────


async def test_sendgrid_accepts_202_and_reads_message_id():
    seen = []
    p = SendGridEmailProvider(
        api_key="SG.x", from_address="a@x.io",
        transport=transport(202, headers={"X-Message-Id": "sg-9"}, capture=seen),
    )
    result = await p.send(email())
    assert result.provider_message_id == "sg-9"
    payload = json.loads(seen[0].content)
    assert payload["personalizations"][0]["to"] == [{"email": "u@x.io"}]
    assert [c["type"] for c in payload["content"]] == ["text/plain", "text/html"]


async def test_sendgrid_forbidden_fails_over():
    p = SendGridEmailProvider(
        api_key="k", from_address="a@x.io", transport=transport(403, {"errors": [{"message": "no"}]})
    )
    with pytest.raises(ProviderSendError) as err:
        await p.send(email())
    assert err.value.retryable is False and err.value.failover is True


async def test_sendgrid_check():
    assert (await check_sendgrid("k", transport(200, {"scopes": []})))[0] is True
    assert (await check_sendgrid("k", transport(401, {})))[0] is False
