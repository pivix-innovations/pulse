from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Protocol

from config.retry import RetrySettings
from domain.entities.dead_letter import DeadLetter
from domain.entities.delivery import DeliveryAttempt
from domain.entities.provider_call import ProviderCall
from telemetry.metrics import get_metrics
from domain.enums import ChannelType, DeliveryStatus
from domain.value_objects.ids import DeadLetterId, DeliveryAttemptId, TenantId, WorkflowId
from jobqueue.retry import compute_backoff
from services.ports.circuit_breaker import CircuitBreaker
from services.ports.dispatch_guard import DispatchGuard
from services.ports.providers import OutboundMessage, ProviderSendError, SendResult
from services.ports.publisher import JobPublisher
from services.ports.uow import UnitOfWork, UnitOfWorkFactory
from services.settlement import settle_notification

logger = logging.getLogger(__name__)


class ResolvedProviderLike(Protocol):
    provider: object
    provider_id: object
    label: str


class ProviderResolver(Protocol):
    """
    Picks the ordered provider chain for a delivery (see providers.registry):
    the workflow's chain of saved providers, else the tenant's default, else
    the env default. Never empty.
    """

    async def resolve_chain(
        self,
        uow: UnitOfWork,
        tenant_id: TenantId,
        channel: ChannelType,
        workflow_id: WorkflowId | None = None,
    ) -> list[ResolvedProviderLike]: ...


class ChainFailed(Exception):
    """Every provider in the chain failed (or one stopped the chain)."""

    def __init__(
        self,
        message: str,
        *,
        retryable: bool,
        invalid: tuple[str, ...],
        calls: list[ProviderCall],
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.invalid_destinations = invalid
        self.calls = calls


class DeliveryService:
    """
    Executes one delivery attempt: idempotency guard, provider dispatch,
    then the pending -> sent transition. A channel's providers are tried in
    the workflow's priority order, moving on only after a hard error or a
    timeout (never after an accepted-but-unconfirmed send). On failure: exponential backoff
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
        breaker: CircuitBreaker | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._providers = providers
        self._guard = guard
        self._publisher = publisher
        self._retry = retry
        self._breaker = breaker

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

            notification = await uow.notifications.get(tenant_id, attempt.notification_id)
            chain = await self._providers.resolve_chain(
                uow,
                tenant_id,
                attempt.channel,
                notification.workflow_id if notification else None,
            )
            # Claim: committed before the provider call so a crash mid-send
            # leaves a marker the reconciliation sweep can find.
            attempt.mark_dispatching(datetime.now(timezone.utc))
            await uow.delivery_attempts.update(attempt)
            await uow.commit()
            try:
                used, result, failed_over, calls = await self._send_through_chain(
                    attempt, chain, message
                )
            except ChainFailed as exc:
                await uow.provider_calls.add_many(exc.calls)
                await self._guard.release(attempt.idempotency_key)
                if exc.invalid_destinations:
                    await self._prune_destinations(uow, attempt, exc.invalid_destinations)
                await self._handle_failure(
                    uow, attempt, error=str(exc), retryable=exc.retryable
                )
                await uow.commit()
                return attempt.status.value
            await uow.provider_calls.add_many(calls)
            if result.invalid_destinations:
                await self._prune_destinations(uow, attempt, result.invalid_destinations)
            provider, provider_id = used.provider, used.provider_id
            if failed_over:
                # Keep the trail: which providers failed before this one sent.
                attempt.last_error = f"Failed over to {used.label} after: " + "; ".join(failed_over)

            attempt.mark_sent(
                datetime.now(timezone.utc),
                provider_id=provider_id,
                expects_confirmation=getattr(provider, "confirms_delivery", True),
                provider_message_id=result.provider_message_id,
            )
            await uow.delivery_attempts.update(attempt)
            if not attempt.expects_confirmation:
                # Fire-and-forget: this may be the last open attempt.
                await settle_notification(uow, tenant_id, attempt.notification_id)
            await uow.commit()
            await self._guard.complete(attempt.idempotency_key)
            get_metrics().deliveries.add(
                1, {"channel": attempt.channel.value, "outcome": "sent", "provider": provider.name}
            )
            logger.info(
                "Attempt %s sent via %s/%s (provider_message_id=%s)",
                attempt_id,
                attempt.channel.value,
                provider.name,
                result.provider_message_id,
            )
            return "sent"

    async def _send_through_chain(
        self, attempt: DeliveryAttempt, chain: list[ResolvedProviderLike], message: OutboundMessage
    ) -> tuple[ResolvedProviderLike, SendResult, list[str], list[ProviderCall]]:
        failures: list[str] = []
        calls: list[ProviderCall] = []

        def record(resolved: ResolvedProviderLike, started: float, outcome: str, error: str | None):
            elapsed_ms = (time.monotonic() - started) * 1000
            attrs = {
                "provider": getattr(resolved.provider, "name", "unknown"),
                "channel": attempt.channel.value,
                "outcome": outcome,
            }
            get_metrics().provider_calls.add(1, attrs)
            get_metrics().provider_duration.record(elapsed_ms, attrs)
            # Only saved providers have a health record; the env default doesn't.
            if resolved.provider_id is not None:
                calls.append(
                    ProviderCall(
                        tenant_id=attempt.tenant_id,
                        provider_id=resolved.provider_id,
                        attempt_id=attempt.id,
                        channel=attempt.channel,
                        outcome=outcome,
                        latency_ms=int((time.monotonic() - started) * 1000),
                        error=error,
                    )
                )

        retryable = False
        invalid: list[str] = []
        chain = await self._gate(chain, failures)
        for position, resolved in enumerate(chain):
            started = time.monotonic()
            try:
                result = await asyncio.wait_for(
                    resolved.provider.send(message), self._retry.provider_timeout_seconds
                )
                record(resolved, started, "ok", None)
                await self._note(resolved, ok=True)
                return resolved, result, failures, calls
            except (asyncio.TimeoutError, TimeoutError):
                error = f"timed out after {self._retry.provider_timeout_seconds:g}s"
                exc_retryable, failover = True, True
            except Exception as exc:
                error = str(exc)
                exc_retryable = getattr(exc, "retryable", True)
                failover = exc.failover if isinstance(exc, ProviderSendError) else True
                invalid.extend(getattr(exc, "invalid_destinations", ()))
            record(resolved, started, "provider_error" if failover else "recipient_error", error[:500])
            if failover:  # a bad recipient says nothing about the provider
                await self._note(resolved, ok=False)
            failures.append(f"{resolved.label}: {error}")
            retryable = retryable or exc_retryable
            if not failover:
                break
            if position + 1 < len(chain):
                logger.warning(
                    "Provider '%s' failed (%s); failing over to '%s'",
                    resolved.label,
                    error,
                    chain[position + 1].label,
                )
        raise ChainFailed(
            "; ".join(failures), retryable=retryable, invalid=tuple(invalid), calls=calls
        )

    async def _gate(
        self, chain: list[ResolvedProviderLike], failures: list[str]
    ) -> list[ResolvedProviderLike]:
        """
        Drop providers whose circuit is open. If that would leave nothing,
        try the whole chain anyway: a pessimistic breaker must never turn a
        partial outage into a total one.
        """
        if self._breaker is None:
            return chain
        usable: list[ResolvedProviderLike] = []
        for resolved in chain:
            if resolved.provider_id is None or await self._breaker.allow(resolved.provider_id):
                usable.append(resolved)
            else:
                failures.append(f"{resolved.label}: skipped (circuit open)")
                logger.info("Skipping provider '%s': circuit open", resolved.label)
        if not usable:
            logger.warning("Every provider circuit is open; trying the chain anyway")
            failures.clear()
            return chain
        return usable

    async def _note(self, resolved: ResolvedProviderLike, *, ok: bool) -> None:
        if self._breaker is None or resolved.provider_id is None:
            return
        try:
            if ok:
                await self._breaker.record_success(resolved.provider_id)
            else:
                await self._breaker.record_failure(resolved.provider_id)
        except Exception:  # the breaker is an optimisation; never fail a delivery over it
            logger.exception("Circuit breaker update failed")

    async def _prune_destinations(
        self, uow: UnitOfWork, attempt: DeliveryAttempt, destinations: tuple[str, ...]
    ) -> None:
        """Drop destinations a provider reported permanently dead (stale push tokens)."""
        if attempt.channel is ChannelType.PUSH:
            removed = await uow.device_tokens.delete_tokens(attempt.tenant_id, list(destinations))
            logger.info("Pruned %d dead device token(s) for attempt %s", removed, attempt.id)

    async def _handle_failure(
        self, uow: UnitOfWork, attempt: DeliveryAttempt, *, error: str, retryable: bool
    ) -> None:
        attempt.mark_failed(error)

        if retryable and attempt.attempt_count < self._retry.max_attempts:
            delay = compute_backoff(attempt.attempt_count, self._retry)
            get_metrics().deliveries.add(
                1, {"channel": attempt.channel.value, "outcome": "retrying", "provider": "n/a"}
            )
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
            get_metrics().deliveries.add(
                1, {"channel": attempt.channel.value, "outcome": "dead", "provider": "n/a"}
            )
            get_metrics().dead_letters.add(1, {"channel": attempt.channel.value})
            logger.warning("Attempt %s dead-lettered: %s", attempt.id, error)
        else:
            # Non-retryable: stays `failed`; the reconciliation sweep may
            # escalate it to the workflow's fallback channel.
            get_metrics().deliveries.add(
                1, {"channel": attempt.channel.value, "outcome": "failed", "provider": "n/a"}
            )
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
