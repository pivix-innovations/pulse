from __future__ import annotations

import json
from typing import Any

from redis.asyncio import Redis

from domain.value_objects.ids import RecipientId, TenantId
from sse.registry import channel_name


class SseBroadcaster:
    """Publishes in-app events to whichever API instance holds the connection."""

    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def publish(
        self, tenant_id: TenantId, recipient_id: RecipientId, event: dict[str, Any]
    ) -> int:
        """Returns the number of live subscribers that received the event."""
        return await self._redis.publish(
            channel_name(tenant_id, recipient_id), json.dumps(event)
        )
