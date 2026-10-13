from __future__ import annotations

from domain.value_objects.ids import NotificationId, TenantId
from services.ports.uow import UnitOfWork


async def settle_notification(
    uow: UnitOfWork, tenant_id: TenantId, notification_id: NotificationId
) -> bool:
    """
    Close out a PROCESSING notification if every attempt is now terminal.
    Caller commits. Returns True if the notification settled.
    """
    notification = await uow.notifications.get(tenant_id, notification_id)
    if notification is None:
        return False
    if await uow.step_runs.has_open(notification_id):
        return False   # a later step of the sequence has yet to run
    if await uow.digests.has_open_for_notification(notification_id):
        return False   # its event is still waiting in an unsent digest
    attempts = await uow.delivery_attempts.list_for_notification(tenant_id, notification_id)
    if not notification.settle(attempts):
        return False
    await uow.notifications.update(notification)
    return True
