from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import WorkflowModel
from domain.entities.workflow import Workflow
from domain.value_objects.ids import TenantId, WorkflowId


def _to_entity(row: WorkflowModel) -> Workflow:
    return Workflow(
        id=WorkflowId(row.id),
        tenant_id=TenantId(row.tenant_id),
        name=row.name,
        event_type=row.event_type,
        definition=dict(row.definition),
        is_active=row.is_active,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class PostgresWorkflowRepository:
    """Implements services.ports.repositories.WorkflowRepository."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, tenant_id: TenantId, workflow_id: WorkflowId) -> Workflow | None:
        stmt = select(WorkflowModel).where(
            WorkflowModel.tenant_id == tenant_id.value,
            WorkflowModel.id == workflow_id.value,
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return _to_entity(row) if row else None

    async def get_active_by_event_type(
        self, tenant_id: TenantId, event_type: str
    ) -> Workflow | None:
        stmt = (
            select(WorkflowModel)
            .where(
                WorkflowModel.tenant_id == tenant_id.value,
                WorkflowModel.event_type == event_type,
                WorkflowModel.is_active.is_(True),
            )
            .order_by(WorkflowModel.created_at)
        )
        row = (await self._session.execute(stmt)).scalars().first()
        return _to_entity(row) if row else None

    async def get_or_create(self, workflow: Workflow) -> Workflow:
        stmt = select(WorkflowModel).where(
            WorkflowModel.tenant_id == workflow.tenant_id.value,
            WorkflowModel.event_type == workflow.event_type,
            WorkflowModel.name == workflow.name,
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is not None:
            return _to_entity(row)

        await self.add(workflow)
        return workflow

    async def add(self, workflow: Workflow) -> None:
        self._session.add(
            WorkflowModel(
                id=workflow.id.value,
                tenant_id=workflow.tenant_id.value,
                name=workflow.name,
                event_type=workflow.event_type,
                definition=workflow.definition,
                is_active=workflow.is_active,
            )
        )
        await self._session.flush()

    async def list(self, tenant_id: TenantId) -> list[Workflow]:
        stmt = (
            select(WorkflowModel)
            .where(WorkflowModel.tenant_id == tenant_id.value)
            .order_by(WorkflowModel.created_at)
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_to_entity(row) for row in rows]

    async def update(self, workflow: Workflow) -> None:
        row = await self._session.get(WorkflowModel, workflow.id.value)
        if row is None or row.tenant_id != workflow.tenant_id.value:
            raise LookupError(f"Workflow {workflow.id} not found for tenant")
        row.name = workflow.name
        row.event_type = workflow.event_type
        row.definition = workflow.definition
        row.is_active = workflow.is_active
        await self._session.flush()

    async def delete(self, tenant_id: TenantId, workflow_id: WorkflowId) -> bool:
        row = await self._session.get(WorkflowModel, workflow_id.value)
        if row is None or row.tenant_id != tenant_id.value:
            return False
        await self._session.delete(row)
        await self._session.flush()
        return True
