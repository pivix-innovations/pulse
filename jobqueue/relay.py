from __future__ import annotations

import asyncio
import logging

from config.outbox import OutboxSettings
from services.ports.publisher import JobPublisher
from services.ports.uow import UnitOfWorkFactory

logger = logging.getLogger(__name__)


class OutboxRelay:
    """
    Polls pending outbox entries and publishes them to the queue.

    Publish happens before mark_published, so a crash in between re-publishes
    the entry on restart -- at-least-once, deduplicated downstream by job id
    and the dispatch idempotency check. Entries are never lost.
    """

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        publisher: JobPublisher,
        settings: OutboxSettings,
    ) -> None:
        self._uow_factory = uow_factory
        self._publisher = publisher
        self._settings = settings
        self._stopped = asyncio.Event()

    async def relay_once(self) -> int:
        """Claim one batch, publish, mark published. Returns entries relayed."""
        async with self._uow_factory() as uow:
            entries = await uow.outbox.claim_pending(self._settings.batch_size)
            if not entries:
                return 0
            published = []
            for entry in entries:
                try:
                    await self._publisher.enqueue(
                        entry.topic, entry.payload, job_id=f"outbox:{entry.id}"
                    )
                    published.append(entry.id)
                except Exception:
                    logger.exception("Failed to publish outbox entry %s", entry.id)
            await uow.outbox.mark_published(published)
            await uow.commit()
            if published:
                logger.info("Relayed %d outbox entr(ies)", len(published))
            return len(published)

    async def run(self) -> None:
        logger.info(
            "Outbox relay started (interval=%.1fs, batch=%d)",
            self._settings.poll_interval_seconds,
            self._settings.batch_size,
        )
        while not self._stopped.is_set():
            try:
                relayed = await self.relay_once()
            except Exception:
                logger.exception("Outbox relay cycle failed")
                relayed = 0
            if relayed == 0:
                try:
                    await asyncio.wait_for(
                        self._stopped.wait(), timeout=self._settings.poll_interval_seconds
                    )
                except asyncio.TimeoutError:
                    pass

    def stop(self) -> None:
        self._stopped.set()
