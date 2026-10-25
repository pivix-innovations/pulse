from __future__ import annotations

from datetime import datetime
from typing import Sequence

from sqlalchemy import exists, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import WorkflowStepRunModel
from domain.entities.step_run import StepRun, StepRunStatus
from domain.value_objects.ids import (
    DeliveryAttemptId,
    NotificationId,
    RecipientId,
    StepRunId,
    TenantId,
)

M = WorkflowStepRunModel


def _to_entity(row: WorkflowStepRunModel) -> StepRun:
    return StepRun(
        id=StepRunId(row.id),
        tenant_id=TenantId(row.tenant_id),
        notification_id=NotificationId(row.notification_id),
        recipient_id=RecipientId(row.recipient_id),
        step_id=row.step_id,
        step_index=row.step_index,
        run_at=row.run_at,
        status=StepRunStatus(row.status),
        attempt_id=DeliveryAttemptId(row.attempt_id) if row.attempt_id else None,
        reason=row.reason,
        executed_at=row.executed_at,
        created_at=row.created_at,
    )


class PostgresStepRunRepository:
    """Implements services.ports.repositories.StepRunRepository."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, run: StepRun) -> bool:
        """Insert; False when this (notification, recipient, step) already has a run."""
        res = await self._session.execute(
            pg_insert(M)
            .values(
                id=run.id.value, tenant_id=run.tenant_id.value,
                notification_id=run.notification_id.value, recipient_id=run.recipient_id.value,
                step_id=run.step_id, step_index=run.step_index, run_at=run.run_at,
                status=run.status.value,
            )
            .on_conflict_do_nothing(constraint="uq_workflow_step_runs_notification_recipient_step")
        )
        return res.rowcount > 0

    async def get(
        self, notification_id: NotificationId, recipient_id: RecipientId, step_id: str
    ) -> StepRun | None:
        row = (
            await self._session.execute(
                select(M).where(
                    M.notification_id == notification_id.value,
                    M.recipient_id == recipient_id.value,
                    M.step_id == step_id,
                )
            )
        ).scalar_one_or_none()
        return _to_entity(row) if row else None

    async def list_for_notification(
        self, tenant_id: TenantId, notification_id: NotificationId
    ) -> list[StepRun]:
        rows = (
            await self._session.execute(
                select(M)
                .where(M.tenant_id == tenant_id.value, M.notification_id == notification_id.value)
                .order_by(M.recipient_id, M.step_index)
            )
        ).scalars().all()
        return [_to_entity(r) for r in rows]

    async def claim_due(self, now: datetime, limit: int) -> list[StepRun]:
        # SKIP LOCKED: several scheduler instances can poll without double-running a step.
        rows = (
            await self._session.execute(
                select(M)
                .where(M.status == StepRunStatus.SCHEDULED.value, M.run_at <= now)
                .order_by(M.run_at)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        ).scalars().all()
        return [_to_entity(r) for r in rows]

    async def has_open(self, notification_id: NotificationId) -> bool:
        return (
            await self._session.execute(
                select(exists().where(M.notification_id == notification_id.value, M.status == "scheduled"))
            )
        ).scalar_one()

    async def save(self, run: StepRun) -> None:
        await self._session.execute(
            update(M)
            .where(M.id == run.id.value)
            .values(
                status=run.status.value,
                attempt_id=run.attempt_id.value if run.attempt_id else None,
                reason=run.reason,
                executed_at=run.executed_at,
            )
        )

    async def cancel_open(self, notification_id: NotificationId, reason: str, at: datetime) -> int:
        res = await self._session.execute(
            update(M)
            .where(M.notification_id == notification_id.value, M.status == "scheduled")
            .values(status=StepRunStatus.SKIPPED.value, reason=reason, executed_at=at)
        )
        return res.rowcount

    async def add_many(self, runs: Sequence[StepRun]) -> None:
        for run in runs:
            await self.add(run)
