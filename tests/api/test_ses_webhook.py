from __future__ import annotations


from domain.enums import ChannelType, DeliveryStatus
from tests.api.test_provider_webhooks import hooked, make_provider, seed_sent_attempt  # noqa: F401
from tests.test_sns_receipts import CERT_URL, TOPIC, keypair, network, signed, ses_event  # noqa: F401

SES = {
    "channel": "email", "name": "ses", "label": "Bulk email",
    "config": {"region": "us-east-1", "from_address": "a@x.io", "sns_topic_arn": TOPIC},
    "secrets": {"access_key_id": "AK", "secret_access_key": "SK"},
}


async def post(world, provider, body):
    return await world.client.post(provider["webhook_path"], content=body)


async def test_ses_delivery_event_confirms_the_attempt(hooked, keypair):  # noqa: F811
    provider = await make_provider(hooked, SES)
    attempt, _ = seed_sent_attempt(hooked, provider["id"], ChannelType.EMAIL, "ses-42")
    r = await post(hooked, provider, signed(keypair[0], ses_event("Delivery")))
    assert r.status_code == 200 and r.json() == {"processed": 1}
    assert attempt.status is DeliveryStatus.DELIVERED


async def test_ses_permanent_bounce_fails_the_attempt_for_fallback(hooked, keypair):  # noqa: F811
    provider = await make_provider(hooked, SES)
    attempt, _ = seed_sent_attempt(hooked, provider["id"], ChannelType.EMAIL, "ses-42")
    event = ses_event("Bounce", bounce={"bounceType": "Permanent"})
    await post(hooked, provider, signed(keypair[0], event))
    assert attempt.status is DeliveryStatus.FAILED


async def test_ses_subscription_confirmation_over_the_endpoint(hooked, keypair, network):  # noqa: F811
    provider = await make_provider(hooked, SES)
    r = await post(hooked, provider, signed(keypair[0], {}, kind="SubscriptionConfirmation"))
    assert r.status_code == 200
    assert any("ConfirmSubscription" in u for u in network)


async def test_ses_event_from_a_foreign_topic_is_unauthorized(hooked, keypair):  # noqa: F811
    provider = await make_provider(hooked, SES)
    body = signed(keypair[0], ses_event("Delivery"), topic="arn:aws:sns:us-east-1:1:other")
    assert (await post(hooked, provider, body)).status_code == 401


async def test_ses_only_expects_confirmation_once_a_topic_is_set(hooked):  # noqa: F811
    from providers.registry import ProviderRegistry
    from domain.entities.provider import Provider
    from domain.value_objects.ids import ProviderId

    box = hooked.app.state.secret_box
    reg = ProviderRegistry(hooked.app.state.settings, secret_box=box)

    def row(config):
        return Provider(
            id=ProviderId.new(), tenant_id=hooked.tenant_id, channel=ChannelType.EMAIL, name="ses",
            label="s", config={"region": "us-east-1", "from_address": "a@x.io", **config},
            secrets_encrypted=box.encrypt({"access_key_id": "AK", "secret_access_key": "SK"}),
        )

    assert reg._build(row({})).confirms_delivery is False
    assert reg._build(row({"sns_topic_arn": TOPIC})).confirms_delivery is True
