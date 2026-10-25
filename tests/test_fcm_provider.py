from __future__ import annotations

import pytest
from firebase_admin import exceptions, messaging

from domain.enums import ChannelType
from providers.push.firebase import AUTH, PERMANENT, STALE, TRANSIENT, FcmPushProvider, classify_fcm_error
from services.ports.providers import OutboundMessage, ProviderSendError


def msg(tokens):
    return OutboundMessage(
        channel=ChannelType.PUSH, to="u1", body_text="b", subject="s",
        metadata={"device_tokens": tokens},
    )


def provider_with(outcomes):
    """FcmPushProvider whose per-token send returns / raises from `outcomes`."""
    p = FcmPushProvider(credentials_json="{}")

    def fake(token, data):
        out = outcomes[token]
        if isinstance(out, Exception):
            raise out
        return out

    p._send_one = fake
    return p


@pytest.mark.parametrize(
    "exc, expected",
    [
        (messaging.UnregisteredError("gone"), STALE),
        (messaging.SenderIdMismatchError("other project"), STALE),
        (exceptions.InvalidArgumentError("malformed"), PERMANENT),
        (messaging.ThirdPartyAuthError("bad apns key"), AUTH),
        (exceptions.UnauthenticatedError("bad creds"), AUTH),
        (exceptions.UnavailableError("down"), TRANSIENT),
        (exceptions.InternalError("500"), TRANSIENT),
        (exceptions.ResourceExhaustedError("quota"), TRANSIENT),
        (ConnectionError("reset"), TRANSIENT),
    ],
)
def test_error_classification(exc, expected):
    assert classify_fcm_error(exc) == expected


async def test_partial_success_reports_dead_tokens_for_pruning():
    p = provider_with({"ok": "msg-1", "dead": messaging.UnregisteredError("gone")})
    result = await p.send(msg(["ok", "dead"]))
    assert result.provider_message_id == "msg-1"
    assert result.invalid_destinations == ("dead",)


async def test_all_tokens_dead_is_permanent_and_does_not_fail_over():
    p = provider_with({"a": messaging.UnregisteredError("x"), "b": messaging.UnregisteredError("y")})
    with pytest.raises(ProviderSendError) as err:
        await p.send(msg(["a", "b"]))
    assert err.value.retryable is False and err.value.failover is False
    assert err.value.invalid_destinations == ("a", "b")


async def test_outage_is_retryable_and_fails_over():
    p = provider_with({"a": exceptions.UnavailableError("down")})
    with pytest.raises(ProviderSendError) as err:
        await p.send(msg(["a"]))
    assert err.value.retryable is True and err.value.failover is True


async def test_bad_credentials_fail_over_without_retry():
    p = provider_with({"a": exceptions.UnauthenticatedError("creds")})
    with pytest.raises(ProviderSendError) as err:
        await p.send(msg(["a"]))
    assert err.value.retryable is False and err.value.failover is True


async def test_no_tokens_is_not_a_provider_fault():
    with pytest.raises(ProviderSendError) as err:
        await provider_with({}).send(msg([]))
    assert err.value.failover is False


async def test_invalid_argument_never_prunes_tokens():
    p = provider_with({"a": exceptions.InvalidArgumentError("payload too big")})
    with pytest.raises(ProviderSendError) as err:
        await p.send(msg(["a"]))
    assert err.value.invalid_destinations == ()
    assert err.value.retryable is False and err.value.failover is False
