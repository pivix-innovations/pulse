from __future__ import annotations

from datetime import datetime, timezone

from domain.entities.delivery import DeliveryAttempt
from domain.enums import DeliveryStatus
from services.ports.uow import UnitOfWork
from services.settlement import settle_notification


async def apply_delivery_update(
    uow: UnitOfWork, attempt: DeliveryAttempt, *, delivered: bool, reason: str | None = None
) -> bool:
    """
    Applies a provider's delivery receipt to an attempt, then settles its
    notification. Idempotent: replays and late/out-of-order callbacks are
    ignored. Returns True when the receipt was consumed (including replays).
    """
    if delivered:
        if attempt.status is DeliveryStatus.DELIVERED:
            return True
        if attempt.status is not DeliveryStatus.SENT:
            return False
        attempt.mark_delivered(datetime.now(timezone.utc))
    else:
        if attempt.status is not DeliveryStatus.SENT:
            return False
        attempt.mark_failed(reason or "provider reported failure")
    await uow.delivery_attempts.update(attempt)
    await settle_notification(uow, attempt.tenant_id, attempt.notification_id)
    return True
