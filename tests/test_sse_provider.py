from __future__ import annotations

import pytest

from domain.enums import ChannelType
from domain.value_objects.ids import RecipientId, TenantId
from providers.in_app.sse import SseInAppProvider
from services.ports.providers import OutboundMessage, ProviderSendError


class FakeBroadcaster:
    def __init__(self, receivers: int) -> None:
        self.receivers = receivers
        self.published: list[tuple] = []

    async def publish(self, tenant_id, recipient_id, event):
        self.published.append((tenant_id, recipient_id, event))
        return self.receivers


def message():
    tenant, recipient = TenantId.new(), RecipientId.new()
    return OutboundMessage(
        channel=ChannelType.IN_APP, to="u1", subject="Hi", body_text="Body",
        metadata={
            "tenant_id": str(tenant), "recipient_id": str(recipient),
            "attempt_id": "att-1", "notification_id": "n-1",
        },
    ), tenant, recipient


async def test_publishes_to_the_recipients_channel_and_reports_receivers():
    msg, tenant, recipient = message()
    bc = FakeBroadcaster(receivers=2)
    result = await SseInAppProvider(bc).send(msg)
    assert result.provider_message_id == "sse-receivers-2"
    [(t, r, event)] = bc.published
    assert (t, r) == (tenant, recipient)
    assert event == {"id": "att-1", "notification_id": "n-1", "title": "Hi", "body": "Body"}


async def test_no_live_connection_is_a_recipient_state_not_a_provider_fault():
    msg, *_ = message()
    with pytest.raises(ProviderSendError) as err:
        await SseInAppProvider(FakeBroadcaster(receivers=0)).send(msg)
    # Not retried and no failover: the channel-level fallback (e.g. push) takes over.
    assert err.value.retryable is False and err.value.failover is False


def test_sse_confirms_delivery_so_the_client_ack_is_required():
    assert SseInAppProvider(FakeBroadcaster(1)).confirms_delivery is True
