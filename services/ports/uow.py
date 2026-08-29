from __future__ import annotations

from typing import Protocol

from services.ports.repositories import (
    DeadLetterRepository,
    DeliveryAttemptRepository,
    DeviceTokenRepository,
    EventRepository,
    MetricsRepository,
    NotificationRepository,
    OutboxRepository,
    PreferenceRepository,
    ProviderRepository,
    RecipientRepository,
    TemplateRepository,
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
    device_tokens: DeviceTokenRepository
    dead_letters: DeadLetterRepository
    templates: TemplateRepository
    providers: ProviderRepository
    metrics: MetricsRepository

    async def __aenter__(self) -> "UnitOfWork": ...

    async def __aexit__(self, exc_type, exc, tb) -> None: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...


class UnitOfWorkFactory(Protocol):
    def __call__(self) -> UnitOfWork: ...
