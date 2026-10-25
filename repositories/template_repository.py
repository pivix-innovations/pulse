from __future__ import annotations

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import TemplateModel, TemplateVersionModel
from domain.entities.template import Template, TemplateVersion
from domain.enums import ChannelType
from domain.value_objects.ids import TemplateId, TemplateVersionId, TenantId


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
        published_version=row.published_version,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _version(row: TemplateVersionModel) -> TemplateVersion:
    return TemplateVersion(
        id=TemplateVersionId(row.id),
        tenant_id=TenantId(row.tenant_id),
        template_id=TemplateId(row.template_id),
        version=row.version,
        subject=row.subject,
        body=row.body,
        format=row.format,
        note=row.note,
        created_at=row.created_at,
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

    async def find(self, tenant_id: TenantId, name: str, channel: ChannelType) -> list[Template]:
        """Every locale of the template with this name and channel."""
        rows = (
            await self._session.execute(
                select(TemplateModel).where(
                    TemplateModel.tenant_id == tenant_id.value,
                    TemplateModel.name == name,
                    TemplateModel.channel == channel,
                )
            )
        ).scalars().all()
        return [_to_entity(r) for r in rows]

    async def add_version(self, version: TemplateVersion) -> TemplateVersion:
        """Append the next version number (the caller's value is replaced) and return the stored version."""
        number = (
            await self._session.execute(
                select(func.coalesce(func.max(TemplateVersionModel.version), 0) + 1).where(
                    TemplateVersionModel.template_id == version.template_id.value
                )
            )
        ).scalar_one()
        row = TemplateVersionModel(
            id=version.id.value, tenant_id=version.tenant_id.value, template_id=version.template_id.value,
            version=number, subject=version.subject, body=version.body, format=version.format, note=version.note,
        )
        self._session.add(row)
        await self._session.flush()
        await self._session.refresh(row)
        return _version(row)

    async def get_version(
        self, tenant_id: TenantId, template_id: TemplateId, version: int
    ) -> TemplateVersion | None:
        row = (
            await self._session.execute(
                select(TemplateVersionModel).where(
                    TemplateVersionModel.tenant_id == tenant_id.value,
                    TemplateVersionModel.template_id == template_id.value,
                    TemplateVersionModel.version == version,
                )
            )
        ).scalar_one_or_none()
        return _version(row) if row else None

    async def list_versions(self, tenant_id: TenantId, template_id: TemplateId) -> list[TemplateVersion]:
        """Newest first."""
        rows = (
            await self._session.execute(
                select(TemplateVersionModel)
                .where(
                    TemplateVersionModel.tenant_id == tenant_id.value,
                    TemplateVersionModel.template_id == template_id.value,
                )
                .order_by(TemplateVersionModel.version.desc())
            )
        ).scalars().all()
        return [_version(r) for r in rows]

    async def list_published(self, tenant_id: TenantId) -> dict[TemplateId, TemplateVersion]:
        """The published version of every template that has one."""
        rows = (
            await self._session.execute(
                select(TemplateVersionModel)
                .join(
                    TemplateModel,
                    (TemplateModel.id == TemplateVersionModel.template_id)
                    & (TemplateModel.published_version == TemplateVersionModel.version),
                )
                .where(TemplateModel.tenant_id == tenant_id.value)
            )
        ).scalars().all()
        return {TemplateId(r.template_id): _version(r) for r in rows}

    async def set_published(self, tenant_id: TenantId, template_id: TemplateId, version: int) -> None:
        row = await self._session.get(TemplateModel, template_id.value)
        if row is None or row.tenant_id != tenant_id.value:
            raise LookupError(f"Template {template_id} not found for tenant")
        row.published_version = version
        await self._session.flush()

    async def delete(self, tenant_id: TenantId, template_id: TemplateId) -> bool:
        row = await self._session.get(TemplateModel, template_id.value)
        if row is None or row.tenant_id != tenant_id.value:
            return False
        await self._session.execute(
            delete(TemplateVersionModel).where(TemplateVersionModel.template_id == template_id.value)
        )
        await self._session.delete(row)
        await self._session.flush()
        return True
