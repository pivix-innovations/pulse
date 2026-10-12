from __future__ import annotations

from typing import Protocol


class DispatchGuard(Protocol):
    """
    Check-and-set on the dispatch idempotency key
    `{notification_id}:{recipient_id}:{channel}` so worker retries can't
    double-send through a provider.
    """

    async def acquire(self, key: str) -> bool:
        """True if this dispatch is ours to perform; False if already done/in flight."""
        ...

    async def complete(self, key: str) -> None:
        """Mark the dispatch as done so duplicates stay blocked long-term."""
        ...

    async def release(self, key: str) -> None:
        """Give the key back after a failed dispatch so a retry may proceed."""
        ...
