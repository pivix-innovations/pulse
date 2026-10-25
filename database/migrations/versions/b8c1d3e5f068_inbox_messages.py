"""persistent in-app inbox

Revision ID: b8c1d3e5f068
Revises: a7f0c2d4e957
Create Date: 2026-10-14

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b8c1d3e5f068"
down_revision: Union[str, Sequence[str], None] = "a7f0c2d4e957"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "inbox_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("recipient_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("notification_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("attempt_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["recipient_id"], ["recipients.id"], name="fk_inbox_messages_recipient_id_recipients"),
        sa.ForeignKeyConstraint(["notification_id"], ["notifications.id"], name="fk_inbox_messages_notification_id_notifications"),
        sa.ForeignKeyConstraint(["attempt_id"], ["delivery_attempts.id"], name="fk_inbox_messages_attempt_id_delivery_attempts"),
        sa.PrimaryKeyConstraint("id", name="pk_inbox_messages"),
        sa.UniqueConstraint("attempt_id", name="uq_inbox_messages_attempt_id"),
    )
    op.create_index("ix_inbox_messages_tenant_id", "inbox_messages", ["tenant_id"])
    op.create_index(
        "ix_inbox_messages_recipient_created", "inbox_messages",
        ["tenant_id", "recipient_id", "created_at", "id"],
    )
    op.create_index(
        "ix_inbox_messages_unread", "inbox_messages", ["tenant_id", "recipient_id"],
        postgresql_where=sa.text("read_at IS NULL AND archived_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_table("inbox_messages")
