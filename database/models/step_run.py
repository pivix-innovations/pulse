from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from database.models.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin


class WorkflowStepRunModel(Base, UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin):
    __tablename__ = "workflow_step_runs"

    notification_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("notifications.id"), nullable=False
    )
    recipient_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("recipients.id"), nullable=False
    )
    step_id: Mapped[str] = mapped_column(String(40), nullable=False)
    step_index: Mapped[int] = mapped_column(nullable=False)
    run_at: Mapped[datetime] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="scheduled")
    attempt_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("delivery_attempts.id"), nullable=True
    )
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    executed_at: Mapped[datetime | None] = mapped_column(nullable=True)

    __table_args__ = (
        # A step runs once per recipient: this is what makes scheduling idempotent.
        UniqueConstraint(
            "notification_id", "recipient_id", "step_id", name="uq_workflow_step_runs_notification_recipient_step"
        ),
        # The scheduler's poll: due runs, oldest first.
        Index("ix_workflow_step_runs_due", "run_at", postgresql_where="status = 'scheduled'"),
        Index("ix_workflow_step_runs_notification", "notification_id"),
    )
