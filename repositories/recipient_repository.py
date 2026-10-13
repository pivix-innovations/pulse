from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import RecipientModel
from domain.entities.recipient import Recipient
from domain.value_objects.ids import RecipientId, TenantId


def _to_entity(row: RecipientModel) -> Recipient:
    return Recipient(
        id=RecipientId(row.id),
        tenant_id=TenantId(row.tenant_id),
        external_ref=row.external_ref,
        email=row.email,
        phone=row.phone,
        locale=row.locale,
        timezone=row.timezone,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class PostgresRecipientRepository:
    """Implements services.ports.repositories.RecipientRepository."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, tenant_id: TenantId, recipient_id: RecipientId) -> Recipient | None:
        stmt = select(RecipientModel).where(
            RecipientModel.tenant_id == tenant_id.value,
            RecipientModel.id == recipient_id.value,
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return _to_entity(row) if row else None

    async def get_by_external_ref(self, tenant_id: TenantId, external_ref: str) -> Recipient | None:
        stmt = select(RecipientModel).where(
            RecipientModel.tenant_id == tenant_id.value,
            RecipientModel.external_ref == external_ref,
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return _to_entity(row) if row else None

    async def upsert_by_external_ref(self, recipient: Recipient) -> Recipient:
        # One atomic statement: a select-then-insert lets two concurrent events for
        # the same brand-new recipient collide on the unique (tenant, external_ref).
        stmt = (
            pg_insert(RecipientModel)
            .values(
                id=recipient.id.value,
                tenant_id=recipient.tenant_id.value,
                external_ref=recipient.external_ref,
                email=recipient.email,
                phone=recipient.phone,
                locale=recipient.locale,
                timezone=recipient.timezone,
            )
            .on_conflict_do_update(
                index_elements=[RecipientModel.tenant_id, RecipientModel.external_ref],
                set_={
                    "email": recipient.email,
                    "phone": recipient.phone,
                    "locale": recipient.locale,
                    "timezone": recipient.timezone,
                    "updated_at": func.now(),
                },
            )
            .returning(RecipientModel)
        )
        row = (await self._session.execute(stmt, execution_options={"populate_existing": True})).scalar_one()
        return _to_entity(row)
