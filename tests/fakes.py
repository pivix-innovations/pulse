"""In-memory fakes implementing the service ports, for unit tests."""
from __future__ import annotations

from dataclasses import dataclass

from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from domain.entities.dead_letter import DeadLetter
from domain.entities.delivery import DeliveryAttempt
from domain.entities.device import DeviceToken
from domain.entities.event import Event
from domain.entities.identity import ApiKey, Membership, Session, Tenant, User
from domain.entities.notification import Notification
from domain.entities.outbox import OutboxEntry
from domain.entities.preference import Preference
from domain.entities.provider import Provider
from domain.entities.recipient import Recipient
from domain.entities.template import Template
from domain.entities.workflow import Workflow
from domain.enums import ChannelType, DeliveryStatus, NotificationStatus, OutboxStatus
from domain.value_objects.ids import (
    ApiKeyId,
    DeadLetterId,
    DeliveryAttemptId,
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
    def __init__(self, attempts: "InMemoryDeliveryAttemptRepository | None" = None) -> None:
        self.items: dict[str, Notification] = {}
        self._attempts = attempts

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
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Notification], int]:
        matches = [
            n
            for n in self.items.values()
            if n.tenant_id == tenant_id and (status is None or n.status is status)
        ]
        return matches[offset : offset + limit], len(matches)

    async def update(self, notification: Notification) -> None:
        self.items[str(notification.id)] = notification


class InMemoryDeliveryAttemptRepository:
    def __init__(self) -> None:
        self.items: dict[str, DeliveryAttempt] = {}

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
        matches = [d for d in self.items if d.tenant_id == tenant_id]
        return matches[offset : offset + limit], len(matches)


class InMemoryTemplateRepository:
    def __init__(self) -> None:
        self.items: dict[str, Template] = {}

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
        return True


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

    async def list_by_ids(
        self, tenant_id: TenantId, provider_ids: list[ProviderId]
    ) -> list[Provider]:
        wanted = {str(i) for i in provider_ids}
        return [
            p for p in self.items.values() if p.tenant_id == tenant_id and str(p.id) in wanted
        ]

    async def get_active_by_channel(
        self, tenant_id: TenantId, channel: ChannelType
    ) -> Provider | None:
        matches = [
            p
            for p in self.items.values()
            if p.tenant_id == tenant_id and p.channel is channel and p.is_active
        ]
        return max(matches, key=lambda p: p.priority, default=None)

    async def update(self, provider: Provider) -> None:
        self.items[str(provider.id)] = provider

    async def delete(self, tenant_id: TenantId, provider_id: ProviderId) -> bool:
        p = self.items.get(str(provider_id))
        if p is None or p.tenant_id != tenant_id:
            return False
        del self.items[str(provider_id)]
        return True


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

    async def list_for_tenant(self, tenant_id: TenantId) -> list[ApiKey]:
        return [k for k in self.items.values() if k.tenant_id == tenant_id]

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
        self.notifications = InMemoryNotificationRepository(self.delivery_attempts)
        self.preferences = InMemoryPreferenceRepository()
        self.events = InMemoryEventRepository()
        self.workflows = InMemoryWorkflowRepository()
        self.recipients = InMemoryRecipientRepository()
        self.outbox = InMemoryOutboxRepository()
        self.device_tokens = InMemoryDeviceTokenRepository()
        self.dead_letters = InMemoryDeadLetterRepository()
        self.templates = InMemoryTemplateRepository()
        self.providers = InMemoryProviderRepository()
        self.metrics = InMemoryMetricsRepository(self)
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
