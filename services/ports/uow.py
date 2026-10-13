from __future__ import annotations

from typing import Protocol

from services.ports.repositories import (
    ApiKeyRepository,
    DeadLetterRepository,
    DeliveryAttemptRepository,
    DeviceTokenRepository,
    EventRepository,
    InboxRepository,
    MembershipRepository,
    MetricsRepository,
    NotificationRepository,
    OperationsRepository,
    OutboxRepository,
    PreferenceRepository,
    ProviderCallRepository,
    ProviderRepository,
    RecipientRepository,
    SessionRepository,
    StepRunRepository,
    TemplateRepository,
    TenantRepository,
    UserRepository,
    WorkflowRepository,
)


class UnitOfWork(Protocol):
    """
    One transaction spanning all repositories. The transactional outbox
    depends on this: notification, attempts, and outbox rows must commit
    atomically.
    """

    notifications: NotificationRepository
    delivery_attempts: DeliveryAttemptRepository
    preferences: PreferenceRepository
    events: EventRepository
    workflows: WorkflowRepository
    recipients: RecipientRepository
    outbox: OutboxRepository
    inbox: InboxRepository
    step_runs: StepRunRepository
    device_tokens: DeviceTokenRepository
    dead_letters: DeadLetterRepository
    templates: TemplateRepository
    providers: ProviderRepository
    provider_calls: ProviderCallRepository
    metrics: MetricsRepository
    operations: OperationsRepository
    tenants: TenantRepository
    users: UserRepository
    memberships: MembershipRepository
    api_keys: ApiKeyRepository
    sessions: SessionRepository

    async def __aenter__(self) -> "UnitOfWork": ...

    async def __aexit__(self, exc_type, exc, tb) -> None: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...


class UnitOfWorkFactory(Protocol):
    def __call__(self) -> UnitOfWork: ...
