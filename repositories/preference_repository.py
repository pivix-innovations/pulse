from __future__ import annotations

from datetime import time

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import PreferenceModel
from domain.entities.preference import Preference
from domain.value_objects.ids import PreferenceId, RecipientId, TenantId
from domain.value_objects.quiet_hours import QuietHours


def _quiet_hours_to_json(qh: QuietHours | None) -> dict | None:
    if qh is None:
        return None
    return {
        "start": qh.start.isoformat(timespec="minutes"),
        "end": qh.end.isoformat(timespec="minutes"),
        "timezone": qh.timezone,
    }


def _quiet_hours_from_json(raw: dict | None) -> QuietHours | None:
    if not raw:
        return None
    return QuietHours(
        start=time.fromisoformat(raw["start"]),
        end=time.fromisoformat(raw["end"]),
        timezone=raw.get("timezone", "UTC"),
    )


def _to_entity(row: PreferenceModel) -> Preference:
    return Preference(
        id=PreferenceId(row.id),
        tenant_id=TenantId(row.tenant_id),
        recipient_id=RecipientId(row.recipient_id),
        channel=row.channel,
        opted_in=row.opted_in,
        quiet_hours=_quiet_hours_from_json(row.quiet_hours),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class PostgresPreferenceRepository:
    """Implements services.ports.repositories.PreferenceRepository."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_for_recipient(
        self, tenant_id: TenantId, recipient_id: RecipientId
    ) -> list[Preference]:
        stmt = select(PreferenceModel).where(
            PreferenceModel.tenant_id == tenant_id.value,
            PreferenceModel.recipient_id == recipient_id.value,
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_to_entity(row) for row in rows]

    async def delete(self, tenant_id: TenantId, preference_id: PreferenceId) -> bool:
        row = await self._session.get(PreferenceModel, preference_id.value)
        if row is None or row.tenant_id != tenant_id.value:
            return False
        await self._session.delete(row)
        await self._session.flush()
        return True

    async def upsert(self, preference: Preference) -> None:
        stmt = insert(PreferenceModel).values(
            id=preference.id.value,
            tenant_id=preference.tenant_id.value,
            recipient_id=preference.recipient_id.value,
            channel=preference.channel.value,
            opted_in=preference.opted_in,
            quiet_hours=_quiet_hours_to_json(preference.quiet_hours),
        )
        stmt = stmt.on_conflict_do_update(
            constraint="uq_preferences_tenant_recipient_channel",
            set_={
                "opted_in": stmt.excluded.opted_in,
                "quiet_hours": stmt.excluded.quiet_hours,
            },
        )
        await self._session.execute(stmt)
        await self._session.flush()
