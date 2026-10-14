"""per-category subscriber preferences

Revision ID: d5f7b9c1e468
Revises: c4e6a8b0d357
Create Date: 2026-10-14

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d5f7b9c1e468"
down_revision: Union[str, Sequence[str], None] = "c4e6a8b0d357"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NOW = sa.text("now()")


def upgrade() -> None:
    op.create_table(
        "category_preferences",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("recipient_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("category", sa.String(length=40), nullable=False),
        sa.Column("channel", sa.String(length=20), nullable=False),
        sa.Column("opted_in", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.ForeignKeyConstraint(["recipient_id"], ["recipients.id"], name="fk_category_preferences_recipient_id_recipients"),
        sa.PrimaryKeyConstraint("id", name="pk_category_preferences"),
        sa.UniqueConstraint("tenant_id", "recipient_id", "category", "channel", name="uq_category_preferences_recipient_category_channel"),
    )
    op.create_index("ix_category_preferences_tenant_id", "category_preferences", ["tenant_id"])


def downgrade() -> None:
    op.drop_table("category_preferences")
