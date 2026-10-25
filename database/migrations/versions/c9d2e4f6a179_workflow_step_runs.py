"""multi-step workflows: per-recipient step runs

Revision ID: c9d2e4f6a179
Revises: b8c1d3e5f068
Create Date: 2026-10-14

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c9d2e4f6a179"
down_revision: Union[str, Sequence[str], None] = "b8c1d3e5f068"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "workflow_step_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("notification_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("recipient_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("step_id", sa.String(length=40), nullable=False),
        sa.Column("step_index", sa.Integer(), nullable=False),
        sa.Column("run_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("attempt_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["notification_id"], ["notifications.id"], name="fk_workflow_step_runs_notification_id_notifications"),
        sa.ForeignKeyConstraint(["recipient_id"], ["recipients.id"], name="fk_workflow_step_runs_recipient_id_recipients"),
        sa.ForeignKeyConstraint(["attempt_id"], ["delivery_attempts.id"], name="fk_workflow_step_runs_attempt_id_delivery_attempts"),
        sa.PrimaryKeyConstraint("id", name="pk_workflow_step_runs"),
        sa.UniqueConstraint(
            "notification_id", "recipient_id", "step_id",
            name="uq_workflow_step_runs_notification_recipient_step",
        ),
    )
    op.create_index("ix_workflow_step_runs_tenant_id", "workflow_step_runs", ["tenant_id"])
    op.create_index(
        "ix_workflow_step_runs_due", "workflow_step_runs", ["run_at"],
        postgresql_where=sa.text("status = 'scheduled'"),
    )
    op.create_index("ix_workflow_step_runs_notification", "workflow_step_runs", ["notification_id"])


def downgrade() -> None:
    op.drop_table("workflow_step_runs")
