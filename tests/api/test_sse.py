from __future__ import annotations

import contextlib

import pytest


class _StopStream(Exception):
    pass


class _ExplodingRegistry:
    """Stops the otherwise endless stream right after the auth + provisioning phase."""

    @contextlib.asynccontextmanager
    async def subscribe(self, tenant_id, recipient_id):
        raise _StopStream
        yield  # pragma: no cover


async def test_stream_requires_credentials(world):
    res = await world.client.get("/api/v1/sse/stream", params={"recipient": "u1"})
    assert res.status_code == 401

    bad = await world.client.get(
        "/api/v1/sse/stream", params={"recipient": "u1"}, headers={"X-API-Key": "pk_wrong"}
    )
    assert bad.status_code == 401


async def test_stream_auto_provisions_unknown_recipient(world):
    world.client._transport.app.state.sse_registry = _ExplodingRegistry()
    assert world.uow.recipients.items == {}

    with pytest.raises(_StopStream):
        async with world.client.stream(
            "GET", "/api/v1/sse/stream", params={"recipient": "brand-new"}, headers=world.key_headers
        ) as res:
            await res.aread()

    refs = [r.external_ref for r in world.uow.recipients.items.values()]
    assert refs == ["brand-new"]
