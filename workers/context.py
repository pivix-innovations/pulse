"""Shared bootstrap for arq worker processes."""
from __future__ import annotations

import logging
from typing import Any

from config.loader import get_settings
from database.database import Database
from providers.registry import ProviderRegistry
from jobqueue.circuit_breaker import RedisCircuitBreaker
from jobqueue.dispatch_guard import RedisDispatchGuard
from jobqueue.producer import ArqJobPublisher
from repositories.uow import SqlAlchemyUnitOfWork
from services.delivery_service import DeliveryService


async def build_worker_context(ctx: dict[str, Any]) -> None:
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()
    ctx["settings"] = settings

    database = Database(settings.database)
    await database.initialize()
    ctx["database"] = database

    def uow_factory() -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(database.session_factory)

    ctx["uow_factory"] = uow_factory
    ctx["delivery_service"] = DeliveryService(
        uow_factory=uow_factory,
        providers=ProviderRegistry(settings, redis=ctx["redis"]),
        guard=RedisDispatchGuard(ctx["redis"]),
        publisher=ArqJobPublisher(ctx["redis"]),
        retry=settings.retry,
        breaker=RedisCircuitBreaker(ctx["redis"], settings.breaker),
    )


async def teardown_worker_context(ctx: dict[str, Any]) -> None:
    await ctx["database"].shutdown()
