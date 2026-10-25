"""In-memory fakes implementing the service ports, for unit tests."""
from __future__ import annotations

from dataclasses import dataclass

from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

from domain.entities.dead_letter import DeadLetter
from domain.entities.delivery import DeliveryAttempt
from domain.entities.device import DeviceToken
from domain.entities.event import Event
from domain.entities.identity import ApiKey, Membership, Session, Tenant, User
from domain.entities.notification import Notification
from domain.entities.digest import DigestBatch, DigestItem, DigestStatus
from domain.entities.inbox import InboxMessage
from domain.entities.step_run import StepRun, StepRunStatus
from domain.entities.outbox import OutboxEntry
from domain.entities.preference import Preference
from domain.entities.suppression import Suppression, normalize_address
from domain.entities.provider import Provider
from domain.entities.recipient import Recipient
from domain.entities.template import Template, TemplateVersion
from domain.entities.workflow import Workflow
from domain.enums import ChannelType, DeliveryStatus, NotificationStatus, OutboxStatus
from domain.value_objects.ids import (
    SuppressionId,
    ApiKeyId,
    DeadLetterId,
    DeliveryAttemptId,
    DigestBatchId,
    DigestItemId,
    EventId,
    MembershipId,
    NotificationId,
    OutboxEntryId,
    PreferenceId,
    ProviderId,
    RecipientId,
    TemplateId,
    TenantId,
    UserId,
    WorkflowId,
)
from services.ports.clients import ResolvedRecipient
from services.ports.providers import OutboundMessage, SendResult


class InMemoryNotificationRepository:
    def __init__(
        self,
        attempts: "InMemoryDeliveryAttemptRepository | None" = None,
        events: "InMemoryEventRepository | None" = None,
    ) -> None:
        self.items: dict[str, Notification] = {}
        self._attempts = attempts
        self._events = events

    async def add(self, notification: Notification) -> None:
        self.items[str(notification.id)] = notification

    async def get(self, tenant_id: TenantId, notification_id: NotificationId) -> Notification | None:
        n = self.items.get(str(notification_id))
        return n if n and n.tenant_id == tenant_id else None

    async def get_by_event(self, tenant_id: TenantId, event_id: EventId) -> Notification | None:
        for n in self.items.values():
            if n.tenant_id == tenant_id and n.event_id == event_id:
                return n
        return None

    async def list_due(self, now: datetime, limit: int) -> list[Notification]:
        due = [
            n
            for n in self.items.values()
            if n.status is NotificationStatus.CREATED
            and n.scheduled_at is not None
            and n.scheduled_at <= now
        ]
        return due[:limit]

    async def list_settleable(self, limit: int) -> list[Notification]:
        attempts = self._attempts.items.values() if self._attempts else []
        return [
            n
            for n in self.items.values()
            if n.status is NotificationStatus.PROCESSING
            and all(a.is_terminal for a in attempts if a.notification_id == n.id)
        ][:limit]

    async def list_for_tenant(
        self,
        tenant_id: TenantId,
        *,
        status: NotificationStatus | None = None,
        event_type: str | None = None,
        recipient_id=None,
        channel=None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Notification], int]:
        attempts = list(self._attempts.items.values()) if self._attempts else []
        event_types = {str(e.id): e.event_type for e in self._events.items.values()} if self._events else {}

        def matches_one(n: Notification) -> bool:
            if n.tenant_id != tenant_id or (status is not None and n.status is not status):
                return False
            if event_type is not None and event_types.get(str(n.event_id)) != event_type:
                return False
            if recipient_id is not None or channel is not None:
                return any(
                    a.notification_id == n.id
                    and (recipient_id is None or a.recipient_id == recipient_id)
                    and (channel is None or a.channel is channel)
                    for a in attempts
                )
            return True

        matches = [n for n in self.items.values() if matches_one(n)]
        return matches[offset : offset + limit], len(matches)

    async def update(self, notification: Notification) -> None:
        self.items[str(notification.id)] = notification


class InMemoryDeliveryAttemptRepository:
    def __init__(self) -> None:
        self.items: dict[str, DeliveryAttempt] = {}

    async def template_usage(self, tenant_id, template_id):
        stats: dict[int, list] = {}
        for a in self.items.values():
            if a.tenant_id == tenant_id and a.template_id == template_id and a.template_version is not None:
                row = stats.setdefault(a.template_version, [0, a.created_at])
                row[0] += 1
                if a.created_at and (row[1] is None or a.created_at > row[1]):
                    row[1] = a.created_at
        return [(v, n, last) for v, (n, last) in sorted(stats.items(), reverse=True)]

    async def add_many(self, attempts: Sequence[DeliveryAttempt]) -> None:
        for a in attempts:
            self.items[str(a.id)] = a

    async def get(
        self, tenant_id: TenantId, attempt_id: DeliveryAttemptId, *, for_update: bool = False
    ) -> DeliveryAttempt | None:
        a = self.items.get(str(attempt_id))
        return a if a and a.tenant_id == tenant_id else None

    async def list_for_notification(
        self, tenant_id: TenantId, notification_id: NotificationId
    ) -> list[DeliveryAttempt]:
        return [
            a
            for a in self.items.values()
            if a.tenant_id == tenant_id and a.notification_id == notification_id
        ]

    async def get_by_provider_message_id(
        self, tenant_id, provider_id, provider_message_id, *, for_update=False
    ):
        for a in self.items.values():
            if (
                a.tenant_id == tenant_id
                and a.provider_id == provider_id
                and a.provider_message_id == provider_message_id
            ):
                return a
        return None

    async def get_by_dispatch(
        self,
        notification_id: NotificationId,
        recipient_id: RecipientId,
        channel: ChannelType,
        *,
        for_update: bool = False,
    ) -> DeliveryAttempt | None:
        for a in self.items.values():
            if (
                a.notification_id == notification_id
                and a.recipient_id == recipient_id
                and a.channel == channel
            ):
                return a
        return None

    async def list_stale(
        self,
        status: DeliveryStatus,
        channel: ChannelType,
        older_than: datetime,
        limit: int,
    ) -> list[DeliveryAttempt]:
        def timestamp(a: DeliveryAttempt) -> datetime | None:
            return a.sent_at if status is DeliveryStatus.SENT else (a.updated_at or a.sent_at)

        matches = [
            a
            for a in self.items.values()
            if a.status is status
            and a.channel is channel
            and (status is not DeliveryStatus.SENT or a.expects_confirmation)
            and (timestamp(a) is None or timestamp(a) < older_than)
        ]
        return matches[:limit]

    async def list_stuck(
        self, lost_before: datetime, in_doubt_before: datetime, limit: int
    ) -> list[DeliveryAttempt]:
        def stuck(a: DeliveryAttempt) -> bool:
            if not a.is_dispatchable:
                return False
            if a.dispatch_started_at is not None:
                return a.dispatch_started_at < in_doubt_before
            due_at = a.next_retry_at or a.updated_at or a.created_at
            return due_at is not None and due_at < lost_before

        return [a for a in self.items.values() if stuck(a)][:limit]

    async def list_unconfirmed_for_recipient(
        self, tenant_id: TenantId, recipient_id: RecipientId, channel: ChannelType
    ) -> list[DeliveryAttempt]:
        return [
            a
            for a in self.items.values()
            if a.tenant_id == tenant_id
            and a.recipient_id == recipient_id
            and a.channel is channel
            and a.status is DeliveryStatus.SENT
        ]

    async def update(self, attempt: DeliveryAttempt) -> None:
        attempt.updated_at = datetime.now(timezone.utc)
        self.items[str(attempt.id)] = attempt


class InMemoryCategoryPreferenceRepository:
    def __init__(self) -> None:
        self.items: list = []

    async def list_for_recipient(self, tenant_id, recipient_id):
        return [p for p in self.items if p.tenant_id == tenant_id and p.recipient_id == recipient_id]

    async def upsert(self, pref) -> None:
        for existing in self.items:
            if (existing.tenant_id, existing.recipient_id, existing.category, existing.channel) == (
                pref.tenant_id, pref.recipient_id, pref.category, pref.channel
            ):
                existing.opted_in = pref.opted_in
                return
        self.items.append(pref)

    async def delete(self, tenant_id, pref_id) -> bool:
        before = len(self.items)
        self.items = [p for p in self.items if not (p.tenant_id == tenant_id and p.id == pref_id)]
        return len(self.items) < before


class InMemoryAuditRepository:
    def __init__(self) -> None:
        self.items: list = []

    async def add(self, entry) -> None:
        self.items.append(entry)

    async def list(self, tenant_id, *, resource_type=None, resource_id=None, limit=50, offset=0):
        rows = [
            e for e in reversed(self.items)
            if e.tenant_id == tenant_id
            and (resource_type is None or e.resource_type == resource_type)
            and (resource_id is None or e.resource_id == resource_id)
        ]
        return rows[offset : offset + limit]


class InMemorySuppressionRepository:
    def __init__(self) -> None:
        self.items: list[Suppression] = []

    async def add(self, suppression: Suppression) -> bool:
        address = normalize_address(suppression.channel, suppression.address)
        if await self.is_suppressed(suppression.tenant_id, suppression.channel, address):
            return False
        suppression.address = address
        self.items.append(suppression)
        return True

    async def is_suppressed(self, tenant_id: TenantId, channel: ChannelType, address: str) -> bool:
        address = normalize_address(channel, address)
        return any(
            s.tenant_id == tenant_id and s.channel == channel and s.address == address for s in self.items
        )

    async def list(self, tenant_id: TenantId, *, limit: int = 50, offset: int = 0) -> list[Suppression]:
        return [s for s in reversed(self.items) if s.tenant_id == tenant_id][offset : offset + limit]

    async def delete(self, tenant_id: TenantId, suppression_id: SuppressionId) -> bool:
        before = len(self.items)
        self.items = [s for s in self.items if not (s.tenant_id == tenant_id and s.id == suppression_id)]
        return len(self.items) < before


class InMemoryPreferenceRepository:
    def __init__(self) -> None:
        self.items: list[Preference] = []

    async def list_for_recipient(
        self, tenant_id: TenantId, recipient_id: RecipientId
    ) -> list[Preference]:
        return [
            p for p in self.items if p.tenant_id == tenant_id and p.recipient_id == recipient_id
        ]

    async def delete(self, tenant_id: TenantId, preference_id: PreferenceId) -> bool:
        before = len(self.items)
        self.items = [
            p for p in self.items if not (p.tenant_id == tenant_id and p.id == preference_id)
        ]
        return len(self.items) < before

    async def upsert(self, preference: Preference) -> None:
        self.items = [
            p
            for p in self.items
            if not (
                p.tenant_id == preference.tenant_id
                and p.recipient_id == preference.recipient_id
                and p.channel == preference.channel
            )
        ]
        self.items.append(preference)


class InMemoryEventRepository:
    def __init__(self) -> None:
        self.items: dict[str, Event] = {}

    async def add(self, event: Event) -> None:
        self.items[str(event.id)] = event

    async def list_for_tenant(
        self, tenant_id: TenantId, *, limit: int = 50, offset: int = 0
    ) -> tuple[list[Event], int]:
        matches = [e for e in self.items.values() if e.tenant_id == tenant_id]
        return matches[offset : offset + limit], len(matches)

    async def get_by_idempotency_key(self, tenant_id: TenantId, key: str) -> Event | None:
        for e in self.items.values():
            if e.tenant_id == tenant_id and e.idempotency_key == key:
                return e
        return None


class InMemoryWorkflowRepository:
    def __init__(self) -> None:
        self.items: dict[str, Workflow] = {}

    async def get(self, tenant_id: TenantId, workflow_id: WorkflowId) -> Workflow | None:
        for w in self.items.values():
            if w.tenant_id == tenant_id and w.id == workflow_id:
                return w
        return None

    async def get_active_by_event_type(
        self, tenant_id: TenantId, event_type: str
    ) -> Workflow | None:
        for w in self.items.values():
            if w.tenant_id == tenant_id and w.event_type == event_type and w.is_active:
                return w
        return None

    async def get_or_create(self, workflow: Workflow) -> Workflow:
        key = f"{workflow.tenant_id}:{workflow.event_type}:{workflow.name}"
        if key not in self.items:
            self.items[key] = workflow
        return self.items[key]

    async def add(self, workflow: Workflow) -> None:
        self.items[f"{workflow.tenant_id}:{workflow.event_type}:{workflow.name}"] = workflow

    async def list(self, tenant_id: TenantId) -> list[Workflow]:
        return [w for w in self.items.values() if w.tenant_id == tenant_id]

    async def update(self, workflow: Workflow) -> None:
        for key, w in list(self.items.items()):
            if w.id == workflow.id:
                self.items[key] = workflow

    async def delete(self, tenant_id: TenantId, workflow_id: WorkflowId) -> bool:
        for key, w in list(self.items.items()):
            if w.tenant_id == tenant_id and w.id == workflow_id:
                del self.items[key]
                return True
        return False


class InMemoryRecipientRepository:
    def __init__(self) -> None:
        self.items: dict[str, Recipient] = {}

    async def get(self, tenant_id: TenantId, recipient_id: RecipientId) -> Recipient | None:
        r = self.items.get(str(recipient_id))
        return r if r and r.tenant_id == tenant_id else None

    async def get_by_external_ref(self, tenant_id: TenantId, external_ref: str) -> Recipient | None:
        for r in self.items.values():
            if r.tenant_id == tenant_id and r.external_ref == external_ref:
                return r
        return None

    async def upsert_by_external_ref(self, recipient: Recipient) -> Recipient:
        for existing in self.items.values():
            if (
                existing.tenant_id == recipient.tenant_id
                and existing.external_ref == recipient.external_ref
            ):
                existing.email = recipient.email
                existing.phone = recipient.phone
                return existing
        self.items[str(recipient.id)] = recipient
        return recipient


class InMemoryOperationsRepository:
    def __init__(self, uow: "InMemoryUnitOfWork") -> None:
        self._uow = uow

    async def snapshot(self, now, lost_before, in_doubt_before):
        from domain.entities.operations import OperationsSnapshot

        pending = [e for e in self._uow.outbox.items.values() if e.status is OutboxStatus.PENDING]
        oldest = min((e.created_at for e in pending if e.created_at), default=None)
        open_attempts: dict = {}
        stuck = 0
        for a in self._uow.delivery_attempts.items.values():
            if a.status in (DeliveryStatus.PENDING, DeliveryStatus.RETRYING, DeliveryStatus.SENT):
                key = (a.channel, a.status)
                open_attempts[key] = open_attempts.get(key, 0) + 1
            if a.status in (DeliveryStatus.PENDING, DeliveryStatus.RETRYING):
                started = getattr(a, "dispatch_started_at", None)
                due = getattr(a, "next_retry_at", None) or getattr(a, "updated_at", None)
                if (started is None and due is not None and due < lost_before) or (
                    started is not None and started < in_doubt_before
                ):
                    stuck += 1
        return OperationsSnapshot(
            outbox_pending=len(pending),
            outbox_oldest_age_seconds=max((now - oldest).total_seconds(), 0.0) if oldest else 0.0,
            open_attempts=open_attempts,
            stuck_attempts=stuck,
            dead_letters=len(self._uow.dead_letters.items),
        )


class InMemoryOutboxRepository:
    def __init__(self) -> None:
        self.items: dict[str, OutboxEntry] = {}

    async def add_many(self, entries: Sequence[OutboxEntry]) -> None:
        for e in entries:
            self.items[str(e.id)] = e

    async def claim_pending(self, limit: int) -> list[OutboxEntry]:
        pending = [e for e in self.items.values() if e.status is OutboxStatus.PENDING]
        return pending[:limit]

    async def mark_published(self, entry_ids: Sequence[OutboxEntryId]) -> None:
        for entry_id in entry_ids:
            self.items[str(entry_id)].mark_published(datetime.now(timezone.utc))


class InMemoryInboxRepository:
    def __init__(self) -> None:
        self.items: dict[str, InboxMessage] = {}
        self._seq = 0

    @staticmethod
    def _mine(m, tenant_id, recipient_id) -> bool:
        return m.tenant_id == tenant_id and m.recipient_id == recipient_id

    async def add(self, message: InboxMessage) -> InboxMessage:
        for existing in self.items.values():
            if existing.attempt_id == message.attempt_id:
                return existing
        self._seq += 1
        # created_at is a server default in Postgres; keep ordering strict here.
        message.created_at = message.created_at or (
            datetime.now(timezone.utc) + timedelta(microseconds=self._seq)
        )
        self.items[str(message.id)] = message
        return message

    async def get(self, tenant_id, recipient_id, message_id):
        m = self.items.get(str(message_id))
        return m if m and self._mine(m, tenant_id, recipient_id) else None

    async def list(self, tenant_id, recipient_id, *, state="active", limit=50, before=None):
        out = [m for m in self.items.values() if self._mine(m, tenant_id, recipient_id)]
        if state == "active":
            out = [m for m in out if not m.is_archived]
        elif state == "unread":
            out = [m for m in out if not m.is_archived and not m.is_read]
        elif state == "read":
            out = [m for m in out if not m.is_archived and m.is_read]
        elif state == "archived":
            out = [m for m in out if m.is_archived]
        out.sort(key=lambda m: (m.created_at, m.id.value), reverse=True)
        if before is not None:
            created, mid = before
            out = [m for m in out if (m.created_at, m.id.value) < (created, mid.value)]
        return out[:limit]

    async def unread_count(self, tenant_id, recipient_id):
        return len(await self.list(tenant_id, recipient_id, state="unread", limit=10**9))

    async def mark_read(self, tenant_id, recipient_id, ids, at):
        wanted = None if ids is None else {str(i) for i in ids}
        changed = []
        for m in self.items.values():
            if not self._mine(m, tenant_id, recipient_id) or m.is_read:
                continue
            if wanted is None and m.is_archived:
                continue
            if wanted is None or str(m.id) in wanted:
                m.read_at = at
                changed.append(m)
        return changed

    async def mark_unread(self, tenant_id, recipient_id, message_id):
        m = await self.get(tenant_id, recipient_id, message_id)
        if m is None:
            return False
        m.read_at = None
        return True

    async def set_archived(self, tenant_id, recipient_id, message_id, at):
        m = await self.get(tenant_id, recipient_id, message_id)
        if m is None:
            return False
        m.archived_at = at
        return True

    async def prune(self, created_before, archived_before):
        gone = [
            k for k, m in self.items.items()
            if m.created_at < created_before or (m.archived_at and m.archived_at < archived_before)
        ]
        for k in gone:
            del self.items[k]
        return len(gone)


class InMemoryStepRunRepository:
    def __init__(self) -> None:
        self.items: dict[str, StepRun] = {}

    async def add(self, run: StepRun) -> bool:
        if any(
            r.notification_id == run.notification_id and r.recipient_id == run.recipient_id
            and r.step_id == run.step_id
            for r in self.items.values()
        ):
            return False
        self.items[str(run.id)] = run
        return True

    async def get(self, notification_id, recipient_id, step_id):
        return next(
            (r for r in self.items.values()
             if r.notification_id == notification_id and r.recipient_id == recipient_id and r.step_id == step_id),
            None,
        )

    async def list_for_notification(self, tenant_id, notification_id):
        runs = [r for r in self.items.values() if r.tenant_id == tenant_id and r.notification_id == notification_id]
        return sorted(runs, key=lambda r: (str(r.recipient_id), r.step_index))

    async def claim_due(self, now, limit):
        due = [r for r in self.items.values() if r.status is StepRunStatus.SCHEDULED and r.run_at <= now]
        return sorted(due, key=lambda r: r.run_at)[:limit]

    async def has_open(self, notification_id):
        return any(
            r.notification_id == notification_id and r.status is StepRunStatus.SCHEDULED
            for r in self.items.values()
        )

    async def save(self, run):
        self.items[str(run.id)] = run

    async def cancel_open(self, notification_id, reason, at):
        n = 0
        for r in self.items.values():
            if r.notification_id == notification_id and r.status is StepRunStatus.SCHEDULED:
                r.mark_skipped(reason, at)
                n += 1
        return n


class InMemoryDigestRepository:
    def __init__(self) -> None:
        self.batches: dict[str, DigestBatch] = {}
        self.items: list[DigestItem] = []

    async def add_item(self, *, tenant_id, workflow_id, recipient_id, digest_key, notification_id,
                       payload, now, window_seconds, max_items):
        batch = next(
            (b for b in self.batches.values()
             if b.status is DigestStatus.OPEN and b.workflow_id == workflow_id
             and b.recipient_id == recipient_id and b.digest_key == digest_key),
            None,
        )
        if batch is None:
            batch = DigestBatch(
                id=DigestBatchId.new(), tenant_id=tenant_id, workflow_id=workflow_id, recipient_id=recipient_id,
                digest_key=digest_key, anchor_notification_id=notification_id,
                flush_at=now + timedelta(seconds=window_seconds), created_at=now,
            )
            self.batches[str(batch.id)] = batch
        self.items.append(DigestItem(
            id=DigestItemId.new(), tenant_id=tenant_id, batch_id=batch.id,
            notification_id=notification_id, payload=payload, created_at=now + timedelta(microseconds=len(self.items)),
        ))
        batch.item_count += 1
        if batch.item_count >= max_items:
            batch.flush_at = min(batch.flush_at, now)
        return batch

    async def claim_due(self, now, limit):
        due = [b for b in self.batches.values() if b.status is DigestStatus.OPEN and b.flush_at <= now]
        return sorted(due, key=lambda b: b.flush_at)[:limit]

    async def list_items(self, batch_id):
        return sorted((i for i in self.items if i.batch_id == batch_id), key=lambda i: i.created_at)

    async def mark_flushed(self, batch, at):
        batch.status, batch.flushed_at = DigestStatus.FLUSHED, at

    async def has_open_for_notification(self, notification_id):
        return any(
            i.notification_id == notification_id and self.batches[str(i.batch_id)].status is DigestStatus.OPEN
            for i in self.items
        )

    async def get_for_notification(self, tenant_id, notification_id):
        for i in self.items:
            if i.notification_id == notification_id and i.tenant_id == tenant_id:
                return self.batches[str(i.batch_id)]
        return None


class InMemoryDeviceTokenRepository:
    def __init__(self) -> None:
        self.items: dict[str, DeviceToken] = {}

    async def upsert(self, token: DeviceToken) -> DeviceToken:
        for existing in self.items.values():
            if existing.tenant_id == token.tenant_id and existing.token == token.token:
                existing.recipient_id = token.recipient_id
                existing.platform = token.platform
                existing.last_seen_at = datetime.now(timezone.utc)
                return existing
        token.last_seen_at = datetime.now(timezone.utc)
        self.items[str(token.id)] = token
        return token

    async def list_for_recipient(
        self, tenant_id: TenantId, recipient_id: RecipientId
    ) -> list[DeviceToken]:
        return [
            t
            for t in self.items.values()
            if t.tenant_id == tenant_id and t.recipient_id == recipient_id
        ]


    async def delete_tokens(self, tenant_id: TenantId, tokens: list[str]) -> int:
        doomed = [
            k for k, t in self.items.items() if t.tenant_id == tenant_id and t.token in tokens
        ]
        for k in doomed:
            del self.items[k]
        return len(doomed)

    async def delete(self, tenant_id, device_id) -> bool:
        t = self.items.get(str(device_id))
        if t is None or t.tenant_id != tenant_id:
            return False
        del self.items[str(device_id)]
        return True


class InMemoryDeadLetterRepository:
    def __init__(self) -> None:
        self.items: list[DeadLetter] = []

    async def add(self, dead_letter: DeadLetter) -> None:
        self.items.append(dead_letter)

    async def get(self, tenant_id: TenantId, dead_letter_id: DeadLetterId) -> DeadLetter | None:
        return next(
            (d for d in self.items if d.tenant_id == tenant_id and d.id == dead_letter_id), None
        )

    async def list_for_tenant(
        self, tenant_id: TenantId, *, limit: int = 50, offset: int = 0
    ) -> tuple[list[DeadLetter], int]:
        # Newest first, like the database (insertion order is oldest first).
        matches = [d for d in reversed(self.items) if d.tenant_id == tenant_id]
        return matches[offset : offset + limit], len(matches)


class InMemoryTemplateRepository:
    def __init__(self) -> None:
        self.items: dict[str, Template] = {}
        self.versions: list[TemplateVersion] = []

    async def add(self, template: Template) -> None:
        self.items[str(template.id)] = template

    async def get(self, tenant_id: TenantId, template_id: TemplateId) -> Template | None:
        t = self.items.get(str(template_id))
        return t if t and t.tenant_id == tenant_id else None

    async def list(self, tenant_id: TenantId) -> list[Template]:
        return [t for t in self.items.values() if t.tenant_id == tenant_id]

    async def update(self, template: Template) -> None:
        self.items[str(template.id)] = template

    async def delete(self, tenant_id: TenantId, template_id: TemplateId) -> bool:
        t = self.items.get(str(template_id))
        if t is None or t.tenant_id != tenant_id:
            return False
        del self.items[str(template_id)]
        self.versions = [v for v in self.versions if v.template_id != template_id]
        return True

    async def find(self, tenant_id, name, channel):
        return [t for t in self.items.values() if t.tenant_id == tenant_id and t.name == name and t.channel is channel]

    async def add_version(self, version):
        mine = [v.version for v in self.versions if v.template_id == version.template_id]
        stored = TemplateVersion(
            id=version.id, tenant_id=version.tenant_id, template_id=version.template_id,
            version=max(mine, default=0) + 1, subject=version.subject, body=version.body,
            format=version.format, note=version.note,
            created_at=datetime.now(timezone.utc) + timedelta(microseconds=len(self.versions)),
        )
        self.versions.append(stored)
        return stored

    async def get_version(self, tenant_id, template_id, version):
        return next(
            (v for v in self.versions
             if v.tenant_id == tenant_id and v.template_id == template_id and v.version == version),
            None,
        )

    async def list_versions(self, tenant_id, template_id):
        mine = [v for v in self.versions if v.tenant_id == tenant_id and v.template_id == template_id]
        return sorted(mine, key=lambda v: v.version, reverse=True)

    async def list_published(self, tenant_id):
        out = {}
        for t in self.items.values():
            if t.tenant_id == tenant_id and t.published_version is not None:
                v = await self.get_version(tenant_id, t.id, t.published_version)
                if v:
                    out[t.id] = v
        return out

    async def set_published(self, tenant_id, template_id, version):
        t = self.items.get(str(template_id))
        if t is None or t.tenant_id != tenant_id:
            raise LookupError("template not found")
        t.published_version = version


class InMemoryProviderRepository:
    def __init__(self) -> None:
        self.items: dict[str, Provider] = {}

    async def add(self, provider: Provider) -> None:
        self.items[str(provider.id)] = provider

    async def get(self, tenant_id: TenantId, provider_id: ProviderId) -> Provider | None:
        p = self.items.get(str(provider_id))
        return p if p and p.tenant_id == tenant_id else None

    async def list(self, tenant_id: TenantId) -> list[Provider]:
        return [p for p in self.items.values() if p.tenant_id == tenant_id]

    async def get_unscoped(self, provider_id: ProviderId) -> Provider | None:
        return self.items.get(str(provider_id))

    async def list_by_ids(
        self, tenant_id: TenantId, provider_ids: list[ProviderId]
    ) -> list[Provider]:
        wanted = {str(i) for i in provider_ids}
        return [
            p for p in self.items.values() if p.tenant_id == tenant_id and str(p.id) in wanted
        ]

    async def update(self, provider: Provider) -> None:
        self.items[str(provider.id)] = provider

    async def delete(self, tenant_id: TenantId, provider_id: ProviderId) -> bool:
        p = self.items.get(str(provider_id))
        if p is None or p.tenant_id != tenant_id:
            return False
        del self.items[str(provider_id)]
        return True


class InMemoryProviderCallRepository:
    def __init__(self) -> None:
        self.items: list = []

    async def add_many(self, calls) -> None:
        now = datetime.now(timezone.utc)
        for c in calls:
            c.created_at = c.created_at or now
            self.items.append(c)

    async def stats(self, tenant_id, since):
        from services.ports.repositories import ProviderCallStats

        window = sorted(
            (c for c in self.items if c.tenant_id == tenant_id and c.created_at >= since),
            key=lambda c: c.created_at,
        )
        out: dict = {}
        latencies: dict = {}
        for c in window:
            s = out.setdefault(c.provider_id, ProviderCallStats())
            if c.outcome == "recipient_error":
                continue  # the provider worked; the destination didn't
            s.calls += 1
            if c.outcome == "provider_error":
                s.failures += 1
                s.last_failure_at, s.last_error = c.created_at, c.error
            else:
                s.last_success_at = c.created_at
                latencies.setdefault(c.provider_id, []).append(c.latency_ms)
            s.recent.insert(0, c.outcome)
        for pid, s in out.items():
            s.recent = s.recent[:5]
            lat = sorted(latencies.get(pid, []))
            if lat:
                s.avg_latency_ms = sum(lat) / len(lat)
                # Continuous percentile, as Postgres' percentile_cont does.
                pos = 0.95 * (len(lat) - 1)
                lo = int(pos)
                hi = min(lo + 1, len(lat) - 1)
                s.p95_latency_ms = lat[lo] + (lat[hi] - lat[lo]) * (pos - lo)
        return out

    async def prune(self, before) -> int:
        keep = [c for c in self.items if c.created_at >= before]
        removed = len(self.items) - len(keep)
        self.items = keep
        return removed


class InMemoryMetricsRepository:
    """Computes aggregates from the sibling in-memory repositories."""

    def __init__(self, uow: "InMemoryUnitOfWork") -> None:
        self._uow = uow

    @staticmethod
    def _within(created_at: datetime | None, since: datetime) -> bool:
        return created_at is None or created_at >= since

    async def attempts_by_channel_status(
        self, tenant_id: TenantId, since: datetime
    ) -> list[tuple[ChannelType, DeliveryStatus, int]]:
        counts: dict[tuple[ChannelType, DeliveryStatus], int] = {}
        for a in self._uow.delivery_attempts.items.values():
            if a.tenant_id == tenant_id and self._within(a.created_at, since):
                counts[(a.channel, a.status)] = counts.get((a.channel, a.status), 0) + 1
        return [(c, s, n) for (c, s), n in counts.items()]

    async def attempts_timeline(
        self, tenant_id: TenantId, since: datetime, bucket_seconds: int
    ) -> list[tuple[datetime, DeliveryStatus, int]]:
        epoch = datetime(2000, 1, 1, tzinfo=timezone.utc)
        counts: dict[tuple[datetime, DeliveryStatus], int] = {}
        for a in self._uow.delivery_attempts.items.values():
            at = a.created_at or datetime.now(timezone.utc)   # a fake attempt has no server-side timestamp
            if a.tenant_id != tenant_id or at < since:
                continue
            start = epoch + timedelta(seconds=((at - epoch).total_seconds() // bucket_seconds) * bucket_seconds)
            counts[(start, a.status)] = counts.get((start, a.status), 0) + 1
        return sorted(((b, s, n) for (b, s), n in counts.items()), key=lambda t: t[0])

    async def notifications_by_status(
        self, tenant_id: TenantId, since: datetime
    ) -> list[tuple[str, int]]:
        counts: dict[str, int] = {}
        for n in self._uow.notifications.items.values():
            if n.tenant_id == tenant_id and self._within(n.created_at, since):
                counts[n.status.value] = counts.get(n.status.value, 0) + 1
        return list(counts.items())

    async def dead_letter_count(self, tenant_id: TenantId, since: datetime) -> int:
        return sum(
            1
            for d in self._uow.dead_letters.items
            if d.tenant_id == tenant_id and self._within(d.created_at, since)
        )

    async def avg_confirmation_seconds(
        self, tenant_id: TenantId, since: datetime
    ) -> float | None:
        latencies = [
            (a.delivered_at - a.sent_at).total_seconds()
            for a in self._uow.delivery_attempts.items.values()
            if a.tenant_id == tenant_id
            and a.delivered_at is not None
            and a.sent_at is not None
            and self._within(a.created_at, since)
        ]
        return sum(latencies) / len(latencies) if latencies else None


class InMemoryTenantRepository:
    def __init__(self) -> None:
        self.items: dict[str, Tenant] = {}

    async def add(self, tenant: Tenant) -> None:
        self.items[str(tenant.id)] = tenant

    async def get(self, tenant_id: TenantId) -> Tenant | None:
        return self.items.get(str(tenant_id))

    async def get_by_slug(self, slug: str) -> Tenant | None:
        return next((t for t in self.items.values() if t.slug == slug), None)

    async def list(self) -> list[Tenant]:
        return list(self.items.values())

    async def update(self, tenant: Tenant) -> None:
        self.items[str(tenant.id)] = tenant


class InMemoryUserRepository:
    def __init__(self) -> None:
        self.items: dict[str, User] = {}

    async def add(self, user: User) -> None:
        self.items[str(user.id)] = user

    async def get(self, user_id: UserId) -> User | None:
        return self.items.get(str(user_id))

    async def get_by_email(self, email: str) -> User | None:
        return next((u for u in self.items.values() if u.email == email.lower()), None)

    async def list(self) -> list[User]:
        return list(self.items.values())

    async def count_platform_admins(self) -> int:
        return sum(1 for u in self.items.values() if u.is_platform_admin and u.is_active)

    async def update(self, user: User) -> None:
        self.items[str(user.id)] = user


class InMemoryMembershipRepository:
    def __init__(self) -> None:
        self.items: dict[str, Membership] = {}

    async def upsert(self, membership: Membership) -> None:
        existing = await self.get(membership.tenant_id, membership.user_id)
        if existing is not None:
            existing.role = membership.role
            return
        self.items[str(membership.id)] = membership

    async def list_for_user(self, user_id: UserId) -> list[Membership]:
        return [m for m in self.items.values() if m.user_id == user_id]

    async def list_for_tenant(self, tenant_id: TenantId) -> list[Membership]:
        return [m for m in self.items.values() if m.tenant_id == tenant_id]

    async def get(self, tenant_id: TenantId, user_id: UserId) -> Membership | None:
        return next(
            (
                m
                for m in self.items.values()
                if m.tenant_id == tenant_id and m.user_id == user_id
            ),
            None,
        )

    async def delete(self, membership_id: MembershipId) -> bool:
        return self.items.pop(str(membership_id), None) is not None


class InMemoryApiKeyRepository:
    def __init__(self) -> None:
        self.items: dict[str, ApiKey] = {}

    async def add(self, key: ApiKey) -> None:
        self.items[str(key.id)] = key

    async def get_by_hash(self, key_hash: str) -> ApiKey | None:
        return next((k for k in self.items.values() if k.key_hash == key_hash), None)

    async def get(self, tenant_id: TenantId, key_id: ApiKeyId) -> ApiKey | None:
        key = self.items.get(str(key_id))
        return key if key and key.tenant_id == tenant_id else None

    async def list_for_tenant(self, tenant_id: TenantId) -> list[ApiKey]:
        return [k for k in self.items.values() if k.tenant_id == tenant_id]

    async def set_expiry(self, tenant_id: TenantId, key_id: ApiKeyId, expires_at) -> bool:
        key = await self.get(tenant_id, key_id)
        if key is None:
            return False
        key.expires_at = expires_at
        return True

    async def touch(self, key_id: ApiKeyId, at) -> None:
        key = self.items.get(str(key_id))
        if key is not None:
            key.last_used_at = at

    async def revoke(self, tenant_id: TenantId, key_id: ApiKeyId) -> bool:
        key = self.items.get(str(key_id))
        if key is None or key.tenant_id != tenant_id:
            return False
        key.is_active = False
        return True


class InMemorySessionRepository:
    def __init__(self) -> None:
        self.items: dict[str, Session] = {}

    async def add(self, sess: Session) -> None:
        self.items[sess.token_hash] = sess

    async def get_by_hash(self, token_hash: str) -> Session | None:
        return self.items.get(token_hash)

    async def revoke(self, token_hash: str, when: datetime) -> None:
        sess = self.items.get(token_hash)
        if sess is not None and sess.revoked_at is None:
            sess.revoked_at = when

    async def delete_expired(self, now: datetime) -> int:
        expired = [h for h, s in self.items.items() if s.expires_at < now]
        for h in expired:
            del self.items[h]
        return len(expired)


class InMemoryUnitOfWork:
    """Implements services.ports.uow.UnitOfWork with dict-backed repos.

    State lives on the instance, so pass `lambda: uow` as the factory to
    inspect state after the service commits.
    """

    def __init__(self) -> None:
        self.delivery_attempts = InMemoryDeliveryAttemptRepository()
        self.events = InMemoryEventRepository()
        self.notifications = InMemoryNotificationRepository(self.delivery_attempts, self.events)
        self.preferences = InMemoryPreferenceRepository()
        self.suppressions = InMemorySuppressionRepository()
        self.audit = InMemoryAuditRepository()
        self.category_preferences = InMemoryCategoryPreferenceRepository()
        self.workflows = InMemoryWorkflowRepository()
        self.recipients = InMemoryRecipientRepository()
        self.outbox = InMemoryOutboxRepository()
        self.inbox = InMemoryInboxRepository()
        self.step_runs = InMemoryStepRunRepository()
        self.digests = InMemoryDigestRepository()
        self.device_tokens = InMemoryDeviceTokenRepository()
        self.dead_letters = InMemoryDeadLetterRepository()
        self.templates = InMemoryTemplateRepository()
        self.providers = InMemoryProviderRepository()
        self.provider_calls = InMemoryProviderCallRepository()
        self.metrics = InMemoryMetricsRepository(self)
        self.operations = InMemoryOperationsRepository(self)
        self.tenants = InMemoryTenantRepository()
        self.users = InMemoryUserRepository()
        self.memberships = InMemoryMembershipRepository()
        self.api_keys = InMemoryApiKeyRepository()
        self.sessions = InMemorySessionRepository()
        self.commits = 0
        self.rollbacks = 0

    async def __aenter__(self) -> "InMemoryUnitOfWork":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


class StaticSentinelClient:
    def __init__(self, recipients: list[ResolvedRecipient]) -> None:
        self._recipients = recipients

    async def resolve_recipients(
        self, tenant_id: TenantId, event_type: str, payload: Mapping[str, Any]
    ) -> list[ResolvedRecipient]:
        return list(self._recipients)


class FakeProvider:
    def __init__(
        self,
        channel: ChannelType = ChannelType.EMAIL,
        fail: bool = False,
        error: Exception | None = None,
        confirms_delivery: bool = True,
    ) -> None:
        self.name = "fake"
        self.channel = channel
        self.confirms_delivery = confirms_delivery
        self.fail = fail
        self.error = error
        self.sent: list[OutboundMessage] = []

    async def send(self, message: OutboundMessage) -> SendResult:
        if self.error is not None:
            raise self.error
        if self.fail:
            raise RuntimeError("provider exploded")
        self.sent.append(message)
        return SendResult(provider_message_id=f"fake-{len(self.sent)}")


@dataclass
class FakeResolved:
    provider: FakeProvider
    provider_id: ProviderId | None = None
    label: str = "fake"


class FakeProviderRegistry:
    """Resolves a fixed chain; pass one provider or an ordered list."""

    def __init__(self, provider: FakeProvider | list[FakeProvider]) -> None:
        providers = provider if isinstance(provider, list) else [provider]
        self.chain = [
            FakeResolved(p, ProviderId.new() if len(providers) > 1 else None, f"p{i}")
            for i, p in enumerate(providers)
        ]

    async def resolve_chain(self, uow, tenant_id, channel, workflow_id=None):
        return self.chain


class FakeDispatchGuard:
    def __init__(self) -> None:
        self.keys: set[str] = set()

    async def acquire(self, key: str) -> bool:
        if key in self.keys:
            return False
        self.keys.add(key)
        return True

    async def complete(self, key: str) -> None:
        self.keys.add(key)

    async def release(self, key: str) -> None:
        self.keys.discard(key)


class FakePublisher:
    def __init__(self) -> None:
        self.published: list[tuple[str, dict, str | None, float | None]] = []

    async def enqueue(
        self,
        topic: str,
        payload: dict,
        *,
        job_id: str | None = None,
        defer_by_seconds: float | None = None,
    ) -> None:
        self.published.append((topic, payload, job_id, defer_by_seconds))


class FakeRateLimiter:
    """In-memory token bucket + daily quota with a controllable clock (same contract as Redis)."""

    def __init__(self) -> None:
        self.now = 1_000_000.0
        self.buckets: dict = {}
        self.quota: dict = {}
        self.fail = False

    async def acquire(self, tenant_id, limits):
        from services.ports.rate_limiter import Decision

        if self.fail:
            raise ConnectionError("redis down")
        tokens, ts = self.buckets.get(tenant_id, (float(limits.burst), self.now))
        tokens = min(limits.burst, tokens + max(0.0, self.now - ts) * limits.rate_per_second)
        if tokens < 1:
            self.buckets[tenant_id] = (tokens, self.now)
            return Decision(False, "rate", (1 - tokens) / limits.rate_per_second, tokens)
        tokens -= 1
        self.buckets[tenant_id] = (tokens, self.now)
        if limits.daily_quota is None:
            return Decision(True, None, 0.0, tokens, None)
        used = self.quota.get(tenant_id, 0) + 1
        if used > limits.daily_quota:
            return Decision(False, "quota", 3600.0, tokens, 0)
        self.quota[tenant_id] = used
        return Decision(True, None, 0.0, tokens, limits.daily_quota - used)

    async def refund_quota(self, tenant_id) -> None:
        if self.quota.get(tenant_id):
            self.quota[tenant_id] -= 1

    async def used_today(self, tenant_id) -> int:
        return self.quota.get(tenant_id, 0)
