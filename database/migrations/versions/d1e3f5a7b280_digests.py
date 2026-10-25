"""digest / batching: open batches and their buffered items

Revision ID: d1e3f5a7b280
Revises: c9d2e4f6a179
Create Date: 2026-10-14

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d1e3f5a7b280"
down_revision: Union[str, Sequence[str], None] = "c9d2e4f6a179"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NOW = sa.text("now()")


def upgrade() -> None:
    op.create_table(
        "digest_batches",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workflow_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("recipient_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("digest_key", sa.String(length=200), nullable=False),
        sa.Column("anchor_notification_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("item_count", sa.Integer(), nullable=False),
        sa.Column("flush_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("flushed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"], name="fk_digest_batches_workflow_id_workflows"),
        sa.ForeignKeyConstraint(["recipient_id"], ["recipients.id"], name="fk_digest_batches_recipient_id_recipients"),
        sa.ForeignKeyConstraint(["anchor_notification_id"], ["notifications.id"], name="fk_digest_batches_anchor_notification_id_notifications"),
        sa.PrimaryKeyConstraint("id", name="pk_digest_batches"),
    )
    op.create_index("ix_digest_batches_tenant_id", "digest_batches", ["tenant_id"])
    op.create_index(
        "uq_digest_batches_open", "digest_batches", ["workflow_id", "recipient_id", "digest_key"],
        unique=True, postgresql_where=sa.text("status = 'open'"),
    )
    op.create_index(
        "ix_digest_batches_due", "digest_batches", ["flush_at"], postgresql_where=sa.text("status = 'open'")
    )
    op.create_table(
        "digest_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("batch_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("notification_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.ForeignKeyConstraint(["batch_id"], ["digest_batches.id"], name="fk_digest_items_batch_id_digest_batches"),
        sa.ForeignKeyConstraint(["notification_id"], ["notifications.id"], name="fk_digest_items_notification_id_notifications"),
        sa.PrimaryKeyConstraint("id", name="pk_digest_items"),
    )
    op.create_index("ix_digest_items_tenant_id", "digest_items", ["tenant_id"])
    op.create_index("ix_digest_items_batch", "digest_items", ["batch_id", "created_at"])
    op.create_index("ix_digest_items_notification", "digest_items", ["notification_id"])


def downgrade() -> None:
    op.drop_table("digest_items")
    op.drop_table("digest_batches")
