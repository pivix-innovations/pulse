from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Protocol

from config.retry import RetrySettings
from domain.entities.dead_letter import DeadLetter
from domain.entities.delivery import DeliveryAttempt
from domain.enums import ChannelType, DeliveryStatus
from domain.value_objects.ids import DeadLetterId, DeliveryAttemptId, TenantId
from jobqueue.retry import compute_backoff
from services.ports.dispatch_guard import DispatchGuard
from services.ports.providers import NotificationProvider, OutboundMessage
from services.ports.publisher import JobPublisher
from services.ports.uow import UnitOfWork, UnitOfWorkFactory

logger = logging.getLogger(__name__)


class ProviderResolver(Protocol):
    """Anything with .get(channel) -> NotificationProvider (see providers.registry)."""

    def get(self, channel: ChannelType) -> NotificationProvider: ...


class DeliveryService:
    """
    Executes one delivery attempt: idempotency guard, provider dispatch,
    then the pending -> sent transition. On failure: exponential backoff
    up to retry.max_attempts, then dead + dead-letter row. At-least-once
    safe. Non-retryable failures stay `failed` for the reconciliation
    sweep to escalate.
    """

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        providers: ProviderResolver,
        guard: DispatchGuard,
        publisher: JobPublisher,
        retry: RetrySettings,
    ) -> None:
        self._uow_factory = uow_factory
        self._providers = providers
        self._guard = guard
        self._publisher = publisher
        self._retry = retry

    async def deliver(self, tenant_id: TenantId, attempt_id: DeliveryAttemptId) -> str:
        async with self._uow_factory() as uow:
            attempt = await uow.delivery_attempts.get(tenant_id, attempt_id)
            if attempt is None:
                logger.warning("Delivery attempt %s not found; dropping job", attempt_id)
                return "missing"
            if attempt.status not in (DeliveryStatus.PENDING, DeliveryStatus.RETRYING):
                return "skipped"

            message = await self._build_message(uow, attempt)
            if message is None:
                await self._handle_failure(
                    uow,
                    attempt,
                    error=f"Recipient has no destination for channel '{attempt.channel.value}'",
                    retryable=False,
                )
                await uow.commit()
                return "failed"

            if not await self._guard.acquire(attempt.idempotency_key):
                return "duplicate"

            provider = self._providers.get(attempt.channel)
            try:
                result = await provider.send(message)
            except Exception as exc:
                await self._guard.release(attempt.idempotency_key)
                await self._handle_failure(
                    uow, attempt, error=str(exc), retryable=getattr(exc, "retryable", True)
                )
                await uow.commit()
                return attempt.status.value

            attempt.mark_sent(datetime.now(timezone.utc))
            await uow.delivery_attempts.update(attempt)
            await uow.commit()
            logger.info(
                "Attempt %s sent via %s/%s (provider_message_id=%s)",
                attempt_id,
                attempt.channel.value,
                provider.name,
                result.provider_message_id,
            )
            return "sent"

    async def _handle_failure(
        self, uow: UnitOfWork, attempt: DeliveryAttempt, *, error: str, retryable: bool
    ) -> None:
        attempt.mark_failed(error)

        if retryable and attempt.attempt_count < self._retry.max_attempts:
            delay = compute_backoff(attempt.attempt_count, self._retry)
            attempt.mark_retrying(datetime.now(timezone.utc) + timedelta(seconds=delay))
            await uow.delivery_attempts.update(attempt)
            await self._publisher.enqueue(
                f"deliver.{attempt.channel.value}",
                {
                    "attempt_id": str(attempt.id),
                    "tenant_id": str(attempt.tenant_id),
                    "channel": attempt.channel.value,
                },
                job_id=f"retry:{attempt.id}:{attempt.attempt_count}",
                defer_by_seconds=delay,
            )
            logger.info(
                "Attempt %s failed (try %d/%d), retrying in %.1fs: %s",
                attempt.id,
                attempt.attempt_count,
                self._retry.max_attempts,
                delay,
                error,
            )
            return

        if retryable:
            # Retries exhausted -> dead-letter.
            attempt.mark_dead(error)
            await uow.dead_letters.add(
                DeadLetter(
                    id=DeadLetterId.new(),
                    tenant_id=attempt.tenant_id,
                    attempt_id=attempt.id,
                    notification_id=attempt.notification_id,
                    channel=attempt.channel,
                    reason=f"Retries exhausted ({attempt.attempt_count}): {error}",
                )
            )
            logger.warning("Attempt %s dead-lettered: %s", attempt.id, error)
        else:
            # Non-retryable: stays `failed`; the reconciliation sweep may
            # escalate it to the workflow's fallback channel.
            logger.info("Attempt %s failed (non-retryable): %s", attempt.id, error)

        await uow.delivery_attempts.update(attempt)

    async def _build_message(
        self, uow: UnitOfWork, attempt: DeliveryAttempt
    ) -> OutboundMessage | None:
        recipient = await uow.recipients.get(attempt.tenant_id, attempt.recipient_id)
        if recipient is None:
            return None

        metadata = {
            "attempt_id": str(attempt.id),
            "notification_id": str(attempt.notification_id),
            "tenant_id": str(attempt.tenant_id),
            "recipient_id": str(attempt.recipient_id),
        }

        if attempt.channel is ChannelType.EMAIL:
            to = recipient.email
        elif attempt.channel is ChannelType.SMS:
            to = recipient.phone
        elif attempt.channel is ChannelType.PUSH:
            tokens = await uow.device_tokens.list_for_recipient(
                attempt.tenant_id, attempt.recipient_id
            )
            if not tokens:
                return None
            metadata["device_tokens"] = [t.token for t in tokens]
            to = recipient.external_ref
        else:  # IN_APP targets the live connection via metadata ids
            to = recipient.external_ref

        if not to:
            return None

        return OutboundMessage(
            channel=attempt.channel,
            to=to,
            subject=attempt.subject,
            body_text=attempt.body or "",
            body_html=attempt.body_html,
            idempotency_key=attempt.idempotency_key,
            metadata=metadata,
        )
