from __future__ import annotations

from dataclasses import dataclass, field

from domain.enums import ChannelType, DeliveryStatus


@dataclass(frozen=True)
class OperationsSnapshot:
    """Cross-tenant health of the delivery pipeline at one instant."""

    outbox_pending: int = 0
    outbox_oldest_age_seconds: float = 0.0
    # Attempts not yet settled, by (channel, status).
    open_attempts: dict[tuple[ChannelType, DeliveryStatus], int] = field(default_factory=dict)
    # Pending/retrying attempts nobody is working (the sweep's input).
    stuck_attempts: int = 0
    dead_letters: int = 0
