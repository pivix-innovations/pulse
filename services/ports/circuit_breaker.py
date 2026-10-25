from __future__ import annotations

from typing import Literal, Protocol

from domain.value_objects.ids import ProviderId

CircuitState = Literal["closed", "open", "half_open"]


class CircuitBreaker(Protocol):
    """Shared (cross-worker) health gate in front of each saved provider."""

    async def allow(self, provider_id: ProviderId) -> bool:
        """True if a message may be sent through the provider now.

        Closed: always. Open: never, until the cooldown ends. Half-open: one
        caller at a time gets to probe; everyone else is turned away.
        """
        ...

    async def record_success(self, provider_id: ProviderId) -> None: ...

    async def record_failure(self, provider_id: ProviderId) -> None: ...

    async def state(self, provider_id: ProviderId) -> CircuitState: ...
