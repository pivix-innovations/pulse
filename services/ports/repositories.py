from __future__ import annotations

from datetime import datetime
from typing import Protocol, Sequence

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
from domain.enums import ChannelType, DeliveryStatus, NotificationStatus
from domain.value_objects.ids import (
    DeliveryAttemptId,
    ApiKeyId,
    DeadLetterId,
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


class NotificationRepository(Protocol):
    """Persistence port for the Notification aggregate."""

    async def add(self, notification: Notification) -> None: ...

    async def get(self, tenant_id: TenantId, notification_id: NotificationId) -> Notification | None: ...

    async def get_by_event(self, tenant_id: TenantId, event_id: EventId) -> Notification | None: ...

    async def list_due(self, now: datetime, limit: int) -> list[Notification]:
        """CREATED notifications whose scheduled_at has arrived (locked, skip-locked)."""
        ...

    async def list_settleable(self, limit: int) -> list[Notification]:
        """PROCESSING notifications with no open attempts left (locked, skip-locked)."""
        ...

    async def list_for_tenant(
        self,
        tenant_id: TenantId,
        *,
        status: NotificationStatus | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Notification], int]:
        """Newest-first page plus the total matching count."""
        ...

    async def update(self, notification: Notification) -> None: ...


class DeliveryAttemptRepository(Protocol):
    """Persistence port for per-(recipient x channel) delivery attempts."""

    async def add_many(self, attempts: Sequence[DeliveryAttempt]) -> None: ...

    async def get_by_dispatch(
        self,
        notification_id: NotificationId,
        recipient_id: RecipientId,
        channel: ChannelType,
        *,
        for_update: bool = False,
    ) -> DeliveryAttempt | None:
        """Look up by the (notification, recipient, channel) idempotency triple."""
        ...

    async def list_stale(
        self,
        status: DeliveryStatus,
        channel: ChannelType,
        older_than: datetime,
        limit: int,
    ) -> list[DeliveryAttempt]:
        """Attempts sitting in `status` since before `older_than` (sweep input)."""
        ...

    async def list_stuck(
        self, lost_before: datetime, in_doubt_before: datetime, limit: int
    ) -> list[DeliveryAttempt]:
        """
        Pending/retrying attempts nobody is working: never claimed and due
        before `lost_before` (job lost), or claimed before `in_doubt_before`
        with no outcome recorded (worker died mid-send). Locked, skip-locked.
        """
        ...

    async def list_unconfirmed_for_recipient(
        self, tenant_id: TenantId, recipient_id: RecipientId, channel: ChannelType
    ) -> list[DeliveryAttempt]:
        """Sent-but-not-delivered attempts, oldest first (SSE replay)."""
        ...

    async def get(
        self, tenant_id: TenantId, attempt_id: DeliveryAttemptId, *, for_update: bool = False
    ) -> DeliveryAttempt | None: ...

    async def list_for_notification(
        self, tenant_id: TenantId, notification_id: NotificationId
    ) -> list[DeliveryAttempt]: ...

    async def update(self, attempt: DeliveryAttempt) -> None: ...


class PreferenceRepository(Protocol):
    """Persistence port for recipient channel preferences."""

    async def list_for_recipient(
        self, tenant_id: TenantId, recipient_id: RecipientId
    ) -> list[Preference]: ...

    async def upsert(self, preference: Preference) -> None: ...

    async def delete(self, tenant_id: TenantId, preference_id: PreferenceId) -> bool: ...


class EventRepository(Protocol):
    """Persistence port for inbound events."""

    async def add(self, event: Event) -> None: ...

    async def list_for_tenant(
        self, tenant_id: TenantId, *, limit: int = 50, offset: int = 0
    ) -> tuple[list[Event], int]:
        """Newest-first page plus the total count."""
        ...

    async def get_by_idempotency_key(self, tenant_id: TenantId, key: str) -> Event | None: ...


class WorkflowRepository(Protocol):
    """Persistence port for workflow rows (registry definitions materialized in DB)."""

    async def get(self, tenant_id: TenantId, workflow_id: WorkflowId) -> Workflow | None: ...

    async def get_active_by_event_type(
        self, tenant_id: TenantId, event_type: str
    ) -> Workflow | None: ...

    async def get_or_create(self, workflow: Workflow) -> Workflow: ...

    async def add(self, workflow: Workflow) -> None: ...

    async def list(self, tenant_id: TenantId) -> list[Workflow]: ...

    async def update(self, workflow: Workflow) -> None: ...

    async def delete(self, tenant_id: TenantId, workflow_id: WorkflowId) -> bool: ...


class TemplateRepository(Protocol):
    """Persistence port for standalone template rows."""

    async def add(self, template: Template) -> None: ...

    async def get(self, tenant_id: TenantId, template_id: TemplateId) -> Template | None: ...

    async def list(self, tenant_id: TenantId) -> list[Template]: ...

    async def update(self, template: Template) -> None: ...

    async def delete(self, tenant_id: TenantId, template_id: TemplateId) -> bool: ...


class ProviderRepository(Protocol):
    """Persistence port for provider configuration rows."""

    async def add(self, provider: Provider) -> None: ...

    async def get(self, tenant_id: TenantId, provider_id: ProviderId) -> Provider | None: ...

    async def list(self, tenant_id: TenantId) -> list[Provider]: ...

    async def list_by_ids(
        self, tenant_id: TenantId, provider_ids: list[ProviderId]
    ) -> list[Provider]:
        """The tenant's providers with these ids (unordered; missing ids are omitted)."""
        ...

    async def get_active_by_channel(
        self, tenant_id: TenantId, channel: ChannelType
    ) -> Provider | None:
        """Highest-priority active provider row for the channel, if any."""
        ...

    async def update(self, provider: Provider) -> None: ...

    async def delete(self, tenant_id: TenantId, provider_id: ProviderId) -> bool: ...


class MetricsRepository(Protocol):
    """Read-only aggregates for the metrics service."""

    async def attempts_by_channel_status(
        self, tenant_id: TenantId, since: datetime
    ) -> list[tuple[ChannelType, DeliveryStatus, int]]: ...

    async def notifications_by_status(
        self, tenant_id: TenantId, since: datetime
    ) -> list[tuple[str, int]]: ...

    async def dead_letter_count(self, tenant_id: TenantId, since: datetime) -> int: ...

    async def avg_confirmation_seconds(
        self, tenant_id: TenantId, since: datetime
    ) -> float | None:
        """Mean sent->delivered latency over confirmed attempts."""
        ...


class RecipientRepository(Protocol):
    """Persistence port for local recipient snapshots."""

    async def get(self, tenant_id: TenantId, recipient_id: RecipientId) -> Recipient | None: ...

    async def get_by_external_ref(self, tenant_id: TenantId, external_ref: str) -> Recipient | None: ...

    async def upsert_by_external_ref(self, recipient: Recipient) -> Recipient: ...


class DeviceTokenRepository(Protocol):
    """Persistence port for push device registrations."""

    async def upsert(self, token: DeviceToken) -> DeviceToken:
        """Insert, or refresh (recipient, platform, last_seen) for an existing token."""
        ...

    async def list_for_recipient(
        self, tenant_id: TenantId, recipient_id: RecipientId
    ) -> list[DeviceToken]: ...

    async def delete_tokens(self, tenant_id: TenantId, tokens: list[str]) -> int:
        """Remove tokens a provider reported dead; returns how many were removed."""
        ...


class DeadLetterRepository(Protocol):
    """Persistence port for the dead-letter table."""

    async def add(self, dead_letter: DeadLetter) -> None: ...

    async def get(self, tenant_id: TenantId, dead_letter_id: DeadLetterId) -> DeadLetter | None: ...

    async def list_for_tenant(
        self, tenant_id: TenantId, *, limit: int = 50, offset: int = 0
    ) -> tuple[list[DeadLetter], int]:
        """Newest-first page plus the total count."""
        ...


class OutboxRepository(Protocol):
    """Persistence port for the transactional outbox."""

    async def add_many(self, entries: Sequence[OutboxEntry]) -> None: ...

    async def claim_pending(self, limit: int) -> list[OutboxEntry]:
        """Lock and return the oldest pending entries (skip already-locked rows)."""
        ...

    async def mark_published(self, entry_ids: Sequence[OutboxEntryId]) -> None: ...


class TenantRepository(Protocol):
    async def add(self, tenant: Tenant) -> None: ...
    async def get(self, tenant_id: TenantId) -> Tenant | None: ...
    async def get_by_slug(self, slug: str) -> Tenant | None: ...
    async def list(self) -> list[Tenant]: ...
    async def update(self, tenant: Tenant) -> None: ...


class UserRepository(Protocol):
    async def add(self, user: User) -> None: ...
    async def get(self, user_id: UserId) -> User | None: ...
    async def get_by_email(self, email: str) -> User | None: ...
    async def list(self) -> list[User]: ...
    async def count_platform_admins(self) -> int: ...
    async def update(self, user: User) -> None: ...


class MembershipRepository(Protocol):
    async def upsert(self, membership: Membership) -> None: ...
    async def list_for_user(self, user_id: UserId) -> list[Membership]: ...
    async def list_for_tenant(self, tenant_id: TenantId) -> list[Membership]: ...
    async def get(self, tenant_id: TenantId, user_id: UserId) -> Membership | None: ...
    async def delete(self, membership_id: MembershipId) -> bool: ...


class ApiKeyRepository(Protocol):
    async def add(self, key: ApiKey) -> None: ...
    async def get_by_hash(self, key_hash: str) -> ApiKey | None: ...
    async def list_for_tenant(self, tenant_id: TenantId) -> list[ApiKey]: ...
    async def revoke(self, tenant_id: TenantId, key_id: ApiKeyId) -> bool: ...


class SessionRepository(Protocol):
    async def add(self, sess: Session) -> None: ...
    async def get_by_hash(self, token_hash: str) -> Session | None: ...
    async def revoke(self, token_hash: str, when: datetime) -> None: ...
    async def delete_expired(self, now: datetime) -> int: ...
