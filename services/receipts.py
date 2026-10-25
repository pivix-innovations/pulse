from __future__ import annotations

from datetime import datetime, timezone

from domain.entities.delivery import DeliveryAttempt
from domain.entities.suppression import Suppression, SuppressionReason
from domain.enums import ChannelType, DeliveryStatus
from domain.value_objects.ids import SuppressionId
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


async def suppress_attempt_address(
    uow: UnitOfWork, attempt: DeliveryAttempt, reason: str, detail: str | None = None
) -> bool:
    """
    A bounce, complaint or STOP reply means the *address* is bad, so later sends to it (from any
    recipient or workflow in the tenant) are skipped. Returns True when a new suppression was recorded.
    """
    recipient = await uow.recipients.get(attempt.tenant_id, attempt.recipient_id)
    if recipient is None:
        return False
    address = recipient.email if attempt.channel is ChannelType.EMAIL else recipient.phone
    if attempt.channel not in (ChannelType.EMAIL, ChannelType.SMS) or not address:
        return False
    return await uow.suppressions.add(
        Suppression(
            id=SuppressionId.new(),
            tenant_id=attempt.tenant_id,
            channel=attempt.channel,
            address=address,
            reason=SuppressionReason(reason),
            detail=(detail or None) and detail[:500],
        )
    )
