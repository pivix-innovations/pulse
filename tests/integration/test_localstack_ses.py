"""
SES delivery events through a real SNS (LocalStack): subscription
confirmation and signature verification against genuine SNS signatures.

LocalStack's free edition has no SES v2 API (a Pro feature), so sending is
covered by botocore-stubbed unit tests instead; this checks the SNS half.

Skipped unless LocalStack is running:

    docker run -d -p 4566:4566 -e SERVICES=sns,ses,sesv2 localstack/localstack:3
    LOCALSTACK_URL=http://localhost:4566 uv run pytest tests/integration
"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import threading
import time

import boto3
import pytest
import uvicorn

from config.api import ApiSettings
from config.loader import get_settings
from config.webhook import WebhookSettings
from domain.enums import ChannelType, DeliveryStatus
from services.secrets import SecretBox, generate_key
from tests.api.test_provider_webhooks import seed_sent_attempt
from tests.api.conftest import world  # noqa: F401  (fixture)

LOCALSTACK = os.environ.get("LOCALSTACK_URL")
pytestmark = pytest.mark.skipif(not LOCALSTACK, reason="set LOCALSTACK_URL to run")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("", 0))
        return s.getsockname()[1]


@pytest.fixture
async def live(world):  # noqa: F811
    """The app served over real HTTP so SNS can call it."""
    port = free_port()
    app = world.app
    app.state.secret_box = SecretBox(generate_key())
    app.state.settings = get_settings().model_copy(update={
        "api": ApiSettings(public_url=f"http://host.docker.internal:{port}"),
        "webhook": WebhookSettings(allow_insecure_sns=True),
    })
    server = uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=port, log_level="warning", lifespan="off"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(50):
        if server.started:
            break
        await asyncio.sleep(0.1)
    yield world, port
    server.should_exit = True
    thread.join(timeout=5)


async def test_subscription_confirmation_and_delivery_event_via_real_sns(live):
    world, port = live  # noqa: F811
    sns = boto3.client(
        "sns", endpoint_url=LOCALSTACK, region_name="us-east-1",
        aws_access_key_id="test", aws_secret_access_key="test",
    )
    topic = sns.create_topic(Name=f"pulse-ses-{port}")["TopicArn"]

    r = await world.client.post("/api/v1/providers", headers=world.key_headers, json={
        "channel": "email", "name": "ses", "label": "LocalStack SES",
        "config": {
            "region": "us-east-1", "from_address": "a@example.com", "sns_topic_arn": topic,
            "endpoint_url": LOCALSTACK,
        },
        "secrets": {"access_key_id": "test", "secret_access_key": "test"},
    })
    assert r.status_code == 201, r.text
    provider = r.json()
    attempt, _ = seed_sent_attempt(world, provider["id"], ChannelType.EMAIL, "ses-ls-1")

    # SNS calls back over HTTP: the confirmation must be verified and followed.
    sns.subscribe(
        TopicArn=topic, Protocol="http",
        Endpoint=f"http://host.docker.internal:{port}{provider['webhook_path']}",
    )
    deadline = time.time() + 20
    while time.time() < deadline:
        subs = sns.list_subscriptions_by_topic(TopicArn=topic)["Subscriptions"]
        if subs and subs[0]["SubscriptionArn"].startswith("arn:"):
            attrs = sns.get_subscription_attributes(SubscriptionArn=subs[0]["SubscriptionArn"])
            if attrs["Attributes"].get("PendingConfirmation") == "false":
                break
        await asyncio.sleep(0.5)
    else:
        pytest.fail("SNS subscription was never confirmed by Pulse")

    sns.publish(TopicArn=topic, Message=json.dumps({
        "eventType": "Delivery", "mail": {"messageId": "ses-ls-1"},
    }))
    deadline = time.time() + 20
    while time.time() < deadline and attempt.status is not DeliveryStatus.DELIVERED:
        await asyncio.sleep(0.5)
    assert attempt.status is DeliveryStatus.DELIVERED

