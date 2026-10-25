from __future__ import annotations

import pytest
from botocore.exceptions import ConnectTimeoutError
from botocore.stub import Stubber

from domain.enums import ChannelType
from providers.email.ses import SesEmailProvider, build_client, check_ses
from services.ports.providers import OutboundMessage, ProviderSendError


def msg():
    return OutboundMessage(
        channel=ChannelType.EMAIL, to="u@x.io", subject="Hi", body_text="Hello", body_html="<p>Hello</p>"
    )


def provider(client, **kw):
    return SesEmailProvider(
        region="us-east-1", access_key_id="AK", secret_access_key="SK",
        from_address="a@x.io", client=client, **kw,
    )


def stubbed():
    client = build_client("us-east-1", "AK", "SK")
    return client, Stubber(client)


async def test_sends_text_and_html_and_returns_message_id():
    client, stub = stubbed()
    stub.add_response(
        "send_email",
        {"MessageId": "ses-1"},
        {
            "FromEmailAddress": "a@x.io",
            "Destination": {"ToAddresses": ["u@x.io"]},
            "Content": {"Simple": {
                "Subject": {"Data": "Hi", "Charset": "UTF-8"},
                "Body": {
                    "Text": {"Data": "Hello", "Charset": "UTF-8"},
                    "Html": {"Data": "<p>Hello</p>", "Charset": "UTF-8"},
                },
            }},
            "ConfigurationSetName": "events",
        },
    )
    with stub:
        result = await provider(client, configuration_set="events").send(msg())
    assert result.provider_message_id == "ses-1"


@pytest.mark.parametrize(
    "code, retryable, failover",
    [
        ("Throttling", True, True),
        ("ServiceUnavailable", True, True),
        ("InvalidClientTokenId", False, True),
        ("AccountSuspendedException", False, True),
        ("MessageRejected", False, True),
        ("BadRequestException", False, False),
    ],
)
async def test_error_codes_map_to_retry_and_failover_semantics(code, retryable, failover):
    client, stub = stubbed()
    stub.add_client_error("send_email", service_error_code=code, service_message="nope")
    with stub, pytest.raises(ProviderSendError) as err:
        await provider(client).send(msg())
    assert (err.value.retryable, err.value.failover) == (retryable, failover)
    assert code in str(err.value)


async def test_network_failures_are_retryable_and_fail_over():
    class Broken:
        def send_email(self, **kw):
            raise ConnectTimeoutError(endpoint_url="https://email.us-east-1.amazonaws.com")

    with pytest.raises(ProviderSendError) as err:
        await provider(Broken()).send(msg())
    assert err.value.retryable is True and err.value.failover is True


async def test_check_reports_sandbox_and_disabled_sending():
    client, stub = stubbed()
    stub.add_response("get_account", {"SendingEnabled": True, "ProductionAccessEnabled": False})
    with stub:
        ok, detail = await check_ses("us-east-1", "AK", "SK", client)
    assert ok and "sandbox" in detail

    client, stub = stubbed()
    stub.add_response("get_account", {"SendingEnabled": False})
    with stub:
        ok, detail = await check_ses("us-east-1", "AK", "SK", client)
    assert not ok and "disabled" in detail

    client, stub = stubbed()
    stub.add_client_error("get_account", service_error_code="InvalidClientTokenId")
    with stub:
        ok, detail = await check_ses("us-east-1", "AK", "SK", client)
    assert not ok and "InvalidClientTokenId" in detail
