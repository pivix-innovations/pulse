from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import TemplateModel
from domain.entities.template import Template
from domain.value_objects.ids import TemplateId, TenantId


def _to_entity(row: TemplateModel) -> Template:
    return Template(
        id=TemplateId(row.id),
        tenant_id=TenantId(row.tenant_id),
        name=row.name,
        channel=row.channel,
        subject=row.subject,
        body=row.body,
        format=row.format,
        locale=row.locale,
        is_active=row.is_active,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class PostgresTemplateRepository:
    """Implements services.ports.repositories.TemplateRepository."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, template: Template) -> None:
        self._session.add(
            TemplateModel(
                id=template.id.value,
                tenant_id=template.tenant_id.value,
                name=template.name,
                channel=template.channel,
                subject=template.subject,
                body=template.body,
                format=template.format,
                locale=template.locale,
                is_active=template.is_active,
            )
        )
        await self._session.flush()

    async def get(self, tenant_id: TenantId, template_id: TemplateId) -> Template | None:
        stmt = select(TemplateModel).where(
            TemplateModel.tenant_id == tenant_id.value,
            TemplateModel.id == template_id.value,
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return _to_entity(row) if row else None

    async def list(self, tenant_id: TenantId) -> list[Template]:
        stmt = (
            select(TemplateModel)
            .where(TemplateModel.tenant_id == tenant_id.value)
            .order_by(TemplateModel.created_at)
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_to_entity(row) for row in rows]

    async def update(self, template: Template) -> None:
        row = await self._session.get(TemplateModel, template.id.value)
        if row is None or row.tenant_id != template.tenant_id.value:
            raise LookupError(f"Template {template.id} not found for tenant")
        row.name = template.name
        row.channel = template.channel
        row.subject = template.subject
        row.body = template.body
        row.format = template.format
        row.locale = template.locale
        row.is_active = template.is_active
        await self._session.flush()

    async def delete(self, tenant_id: TenantId, template_id: TemplateId) -> bool:
        row = await self._session.get(TemplateModel, template_id.value)
        if row is None or row.tenant_id != tenant_id.value:
            return False
        await self._session.delete(row)
        await self._session.flush()
        return True
