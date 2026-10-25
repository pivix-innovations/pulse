"""provider call log (per-call outcome and latency) for provider health

Revision ID: e4c7f9a1b624
Revises: d2b6e8f0a513
Create Date: 2026-10-13

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e4c7f9a1b624"
down_revision: Union[str, Sequence[str], None] = "d2b6e8f0a513"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "provider_calls",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False, index=True),
        sa.Column(
            "provider_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("providers.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("attempt_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel", sa.String(20), nullable=False),
        sa.Column("outcome", sa.String(20), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "ix_provider_calls_provider_created", "provider_calls",
        ["tenant_id", "provider_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_provider_calls_provider_created", table_name="provider_calls")
    op.drop_table("provider_calls")
