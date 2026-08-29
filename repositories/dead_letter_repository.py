from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from database.models import DeadLetterModel
from domain.entities.dead_letter import DeadLetter


class PostgresDeadLetterRepository:
    """Implements services.ports.repositories.DeadLetterRepository."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, dead_letter: DeadLetter) -> None:
        self._session.add(
            DeadLetterModel(
                id=dead_letter.id.value,
                tenant_id=dead_letter.tenant_id.value,
                attempt_id=dead_letter.attempt_id.value,
                notification_id=dead_letter.notification_id.value,
                channel=dead_letter.channel,
                reason=dead_letter.reason,
            )
        )
        await self._session.flush()
