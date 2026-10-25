"""Shared bootstrap for arq worker processes."""
from __future__ import annotations

from typing import Any

from config.loader import get_settings
from database.database import Database
from providers.registry import ProviderRegistry
from jobqueue.circuit_breaker import RedisCircuitBreaker
from jobqueue.dispatch_guard import RedisDispatchGuard
from jobqueue.producer import ArqJobPublisher
from repositories.uow import SqlAlchemyUnitOfWork
from services.delivery_service import DeliveryService
from telemetry.log_context import configure_logging
from telemetry.metrics import configure_metrics
from telemetry.tracing import configure_tracing, shutdown_tracing


async def build_worker_context(ctx: dict[str, Any], name: str = "worker") -> None:
    settings = get_settings()
    configure_logging(settings.logging)
    ctx["settings"] = settings
    configure_tracing(settings.otel, service_name=f"pulse-{name}-worker")
    ctx["telemetry"] = configure_metrics(
        settings.otel, service_name=f"pulse-{name}-worker"
    )

    database = Database(settings.database)
    await database.initialize()
    ctx["database"] = database

    def uow_factory() -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(database.session_factory)

    ctx["uow_factory"] = uow_factory
    ctx["delivery_service"] = DeliveryService(
        uow_factory=uow_factory,
        providers=ProviderRegistry(settings, redis=ctx["redis"]),
        guard=RedisDispatchGuard(ctx["redis"], lease_seconds=settings.retry.dispatch_lease_seconds),
        publisher=ArqJobPublisher(ctx["redis"]),
        retry=settings.retry,
        breaker=RedisCircuitBreaker(ctx["redis"], settings.breaker),
    )


async def teardown_worker_context(ctx: dict[str, Any]) -> None:
    shutdown_tracing()
    if ctx.get("telemetry") is not None:
        ctx["telemetry"].shutdown()
    await ctx["database"].shutdown()
