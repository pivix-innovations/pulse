from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from database.models.category_preference import CategoryPreferenceModel
from domain.entities.category_preference import CategoryPreference
from domain.value_objects.ids import CategoryPreferenceId, RecipientId, TenantId


def _to_entity(r: CategoryPreferenceModel) -> CategoryPreference:
    return CategoryPreference(
        id=CategoryPreferenceId(r.id), tenant_id=TenantId(r.tenant_id), recipient_id=RecipientId(r.recipient_id),
        category=r.category, channel=r.channel, opted_in=r.opted_in, created_at=r.created_at, updated_at=r.updated_at,
    )


class PostgresCategoryPreferenceRepository:
    """Implements services.ports.repositories.CategoryPreferenceRepository."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_for_recipient(self, tenant_id: TenantId, recipient_id: RecipientId) -> list[CategoryPreference]:
        stmt = (
            select(CategoryPreferenceModel)
            .where(
                CategoryPreferenceModel.tenant_id == tenant_id.value,
                CategoryPreferenceModel.recipient_id == recipient_id.value,
            )
            .order_by(CategoryPreferenceModel.category, CategoryPreferenceModel.channel)
        )
        return [_to_entity(r) for r in (await self._session.execute(stmt)).scalars()]

    async def upsert(self, pref: CategoryPreference) -> None:
        stmt = (
            insert(CategoryPreferenceModel)
            .values(
                id=pref.id.value, tenant_id=pref.tenant_id.value, recipient_id=pref.recipient_id.value,
                category=pref.category, channel=pref.channel, opted_in=pref.opted_in,
            )
            .on_conflict_do_update(
                constraint="uq_category_preferences_recipient_category_channel",
                set_={"opted_in": pref.opted_in},
            )
        )
        await self._session.execute(stmt)

    async def delete(self, tenant_id: TenantId, pref_id: CategoryPreferenceId) -> bool:
        result = await self._session.execute(
            delete(CategoryPreferenceModel).where(
                CategoryPreferenceModel.tenant_id == tenant_id.value, CategoryPreferenceModel.id == pref_id.value
            )
        )
        return result.rowcount > 0
