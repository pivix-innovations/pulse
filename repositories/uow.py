from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from repositories.dead_letter_repository import PostgresDeadLetterRepository
from repositories.delivery_repository import PostgresDeliveryAttemptRepository
from repositories.device_token_repository import PostgresDeviceTokenRepository
from repositories.digest_repository import PostgresDigestRepository
from repositories.event_repository import PostgresEventRepository
from repositories.identity_repository import (
    PostgresApiKeyRepository,
    PostgresMembershipRepository,
    PostgresSessionRepository,
    PostgresTenantRepository,
    PostgresUserRepository,
)
from repositories.inbox_repository import PostgresInboxRepository
from repositories.metrics_repository import PostgresMetricsRepository
from repositories.notification_repository import PostgresNotificationRepository
from repositories.operations_repository import PostgresOperationsRepository
from repositories.outbox_repository import PostgresOutboxRepository
from repositories.preference_repository import PostgresPreferenceRepository
from repositories.audit_repository import PostgresAuditRepository
from repositories.suppression_repository import PostgresSuppressionRepository
from repositories.provider_call_repository import PostgresProviderCallRepository
from repositories.provider_repository import PostgresProviderRepository
from repositories.recipient_repository import PostgresRecipientRepository
from repositories.step_run_repository import PostgresStepRunRepository
from repositories.template_repository import PostgresTemplateRepository
from repositories.workflow_repository import PostgresWorkflowRepository


class SqlAlchemyUnitOfWork:
    """Implements services.ports.uow.UnitOfWork over one AsyncSession."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        self._session: AsyncSession | None = None

    @property
    def session(self) -> AsyncSession:
        if self._session is None:
            raise RuntimeError("UnitOfWork is not active; use 'async with'")
        return self._session

    async def __aenter__(self) -> "SqlAlchemyUnitOfWork":
        self._session = self._session_factory()
        self.notifications = PostgresNotificationRepository(self._session)
        self.delivery_attempts = PostgresDeliveryAttemptRepository(self._session)
        self.preferences = PostgresPreferenceRepository(self._session)
        self.suppressions = PostgresSuppressionRepository(self._session)
        self.audit = PostgresAuditRepository(self._session)
        self.events = PostgresEventRepository(self._session)
        self.workflows = PostgresWorkflowRepository(self._session)
        self.recipients = PostgresRecipientRepository(self._session)
        self.outbox = PostgresOutboxRepository(self._session)
        self.inbox = PostgresInboxRepository(self._session)
        self.step_runs = PostgresStepRunRepository(self._session)
        self.digests = PostgresDigestRepository(self._session)
        self.device_tokens = PostgresDeviceTokenRepository(self._session)
        self.dead_letters = PostgresDeadLetterRepository(self._session)
        self.templates = PostgresTemplateRepository(self._session)
        self.providers = PostgresProviderRepository(self._session)
        self.provider_calls = PostgresProviderCallRepository(self._session)
        self.metrics = PostgresMetricsRepository(self._session)
        self.operations = PostgresOperationsRepository(self._session)
        self.tenants = PostgresTenantRepository(self._session)
        self.users = PostgresUserRepository(self._session)
        self.memberships = PostgresMembershipRepository(self._session)
        self.api_keys = PostgresApiKeyRepository(self._session)
        self.sessions = PostgresSessionRepository(self._session)
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        try:
            if exc_type is not None:
                await self.session.rollback()
        finally:
            await self.session.close()
            self._session = None

    async def commit(self) -> None:
        await self.session.commit()

    async def rollback(self) -> None:
        await self.session.rollback()
