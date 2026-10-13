"""Recipient-facing inbox operations (list, read state, archive)."""
from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timezone

from domain.entities.inbox import InboxMessage
from domain.enums import DeliveryStatus
from domain.value_objects.ids import InboxMessageId, InvalidIdError, RecipientId, TenantId
from services.ports.uow import UnitOfWork, UnitOfWorkFactory
from services.settlement import settle_notification

STATES = ("active", "unread", "read", "archived", "all")


class InboxNotFound(Exception):
    """No such recipient or message (also what another tenant's id looks like)."""


class InvalidCursor(ValueError):
    pass


@dataclass(frozen=True)
class InboxPage:
    items: list[InboxMessage]
    next_cursor: str | None
    unread_count: int


def encode_cursor(message: InboxMessage) -> str:
    raw = f"{message.created_at.isoformat()}|{message.id}"
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, InboxMessageId]:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
        stamp, _, mid = raw.partition("|")
        return datetime.fromisoformat(stamp), InboxMessageId.from_str(mid)
    except (ValueError, InvalidIdError, UnicodeDecodeError) as exc:
        raise InvalidCursor("Malformed cursor") from exc


class InboxService:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def _recipient(self, uow: UnitOfWork, tenant_id: TenantId, external_ref: str) -> RecipientId:
        recipient = await uow.recipients.get_by_external_ref(tenant_id, external_ref)
        if recipient is None:
            raise InboxNotFound("Recipient not found")
        return recipient.id

    async def list(
        self,
        tenant_id: TenantId,
        external_ref: str,
        *,
        state: str = "active",
        limit: int = 30,
        cursor: str | None = None,
    ) -> InboxPage:
        before = decode_cursor(cursor) if cursor else None
        async with self._uow_factory() as uow:
            try:
                recipient_id = await self._recipient(uow, tenant_id, external_ref)
            except InboxNotFound:
                return InboxPage([], None, 0)  # clients may ask before their first notification
            rows = await uow.inbox.list(
                tenant_id, recipient_id, state=state, limit=limit + 1, before=before
            )
            more = len(rows) > limit
            items = rows[:limit]
            return InboxPage(
                items=items,
                next_cursor=encode_cursor(items[-1]) if more and items else None,
                unread_count=await uow.inbox.unread_count(tenant_id, recipient_id),
            )

    async def unread_count(self, tenant_id: TenantId, external_ref: str) -> int:
        async with self._uow_factory() as uow:
            try:
                recipient_id = await self._recipient(uow, tenant_id, external_ref)
            except InboxNotFound:
                return 0
            return await uow.inbox.unread_count(tenant_id, recipient_id)

    async def mark_read(
        self, tenant_id: TenantId, external_ref: str, ids: list[InboxMessageId] | None
    ) -> int:
        """
        Reading is the confirmation the sweep waits for: the matching in-app
        attempts become `delivered`, so a message read in time is never
        escalated to the fallback channel. Returns how many changed.
        """
        now = datetime.now(timezone.utc)
        async with self._uow_factory() as uow:
            recipient_id = await self._recipient(uow, tenant_id, external_ref)
            if ids is not None:
                for message_id in ids:
                    if await uow.inbox.get(tenant_id, recipient_id, message_id) is None:
                        raise InboxNotFound("Message not found")
            changed = await uow.inbox.mark_read(tenant_id, recipient_id, ids, now)
            for message in changed:
                attempt = await uow.delivery_attempts.get(tenant_id, message.attempt_id, for_update=True)
                if attempt is not None and attempt.status is DeliveryStatus.SENT:
                    attempt.mark_delivered(now)
                    await uow.delivery_attempts.update(attempt)
                    await settle_notification(uow, tenant_id, attempt.notification_id)
            await uow.commit()
            return len(changed)

    async def mark_unread(self, tenant_id: TenantId, external_ref: str, message_id: InboxMessageId) -> None:
        async with self._uow_factory() as uow:
            recipient_id = await self._recipient(uow, tenant_id, external_ref)
            if not await uow.inbox.mark_unread(tenant_id, recipient_id, message_id):
                raise InboxNotFound("Message not found")
            await uow.commit()

    async def set_archived(
        self, tenant_id: TenantId, external_ref: str, message_id: InboxMessageId, archived: bool
    ) -> None:
        async with self._uow_factory() as uow:
            recipient_id = await self._recipient(uow, tenant_id, external_ref)
            at = datetime.now(timezone.utc) if archived else None
            if not await uow.inbox.set_archived(tenant_id, recipient_id, message_id, at):
                raise InboxNotFound("Message not found")
            await uow.commit()
