"""
Scheduler process: hosts the outbox relay, the reconciliation sweep, and
the scheduled-notification dispatcher.

Run with:  uv run python -m workers.scheduler_worker
"""
from __future__ import annotations

import asyncio
import logging
import signal
from datetime import datetime, timedelta, timezone

from clients.sentinel_client import DatabaseRecipientClient
from config.loader import get_settings
from database.database import Database
from jobqueue.producer import ArqJobPublisher, create_arq_pool
from jobqueue.relay import OutboxRelay
from repositories.uow import SqlAlchemyUnitOfWork
from services.notification_service import NotificationService
from services.reconciliation_service import ReconciliationService
from services.rendering_service import RenderingService
from services.scheduling_service import SchedulingService
from workflows.registry import WorkflowRegistry

SCHEDULE_POLL_SECONDS = 5.0

logger = logging.getLogger(__name__)


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()

    database = Database(settings.database)
    await database.initialize()
    pool = await create_arq_pool(settings.redis)

    def uow_factory() -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(database.session_factory)

    relay = OutboxRelay(uow_factory, ArqJobPublisher(pool), settings.outbox)
    sweep = ReconciliationService(uow_factory, RenderingService(), settings.reconciliation)
    sentinel = DatabaseRecipientClient(uow_factory)
    scheduler = SchedulingService(
        uow_factory=uow_factory,
        notifications=NotificationService(
            uow_factory=uow_factory,
            sentinel=sentinel,
            registry=WorkflowRegistry(),
            rendering=RenderingService(),
        ),
        sentinel=sentinel,
    )

    stop = asyncio.Event()

    async def sweep_loop() -> None:
        logger.info(
            "Reconciliation sweep started (interval=%.1fs)",
            settings.reconciliation.interval_seconds,
        )
        while not stop.is_set():
            try:
                await sweep.sweep_once()
            except Exception:
                logger.exception("Reconciliation sweep cycle failed")
            try:
                await asyncio.wait_for(
                    stop.wait(), timeout=settings.reconciliation.interval_seconds
                )
            except asyncio.TimeoutError:
                pass

    async def schedule_loop() -> None:
        logger.info("Scheduled-notification dispatcher started (interval=%.1fs)", SCHEDULE_POLL_SECONDS)
        while not stop.is_set():
            try:
                await scheduler.dispatch_due_once()
            except Exception:
                logger.exception("Scheduled dispatch cycle failed")
            try:
                await asyncio.wait_for(stop.wait(), timeout=SCHEDULE_POLL_SECONDS)
            except asyncio.TimeoutError:
                pass

    async def prune_loop() -> None:
        while not stop.is_set():
            try:
                cutoff = datetime.now(timezone.utc) - timedelta(
                    days=settings.reconciliation.provider_call_retention_days
                )
                async with uow_factory() as uow:
                    removed = await uow.provider_calls.prune(cutoff)
                    await uow.commit()
                if removed:
                    logger.info("Pruned %d provider call log row(s)", removed)
            except Exception:
                logger.exception("Provider call prune failed")
            try:
                await asyncio.wait_for(
                    stop.wait(),
                    timeout=settings.reconciliation.provider_call_prune_interval_seconds,
                )
            except asyncio.TimeoutError:
                pass

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    tasks = [
        asyncio.create_task(relay.run()),
        asyncio.create_task(sweep_loop()),
        asyncio.create_task(schedule_loop()),
        asyncio.create_task(prune_loop()),
    ]
    await stop.wait()
    relay.stop()
    await asyncio.gather(*tasks)
    await pool.aclose()
    await database.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
