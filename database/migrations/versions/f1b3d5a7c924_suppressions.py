"""suppression list: addresses that must not be sent to

Revision ID: f1b3d5a7c924
Revises: e5f2a4b6c391
Create Date: 2026-10-14

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f1b3d5a7c924"
down_revision: Union[str, Sequence[str], None] = "e5f2a4b6c391"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NOW = sa.text("now()")


def upgrade() -> None:
    op.create_table(
        "suppressions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel", sa.Enum("email", "push", "sms", "in_app", name="channeltype", native_enum=False, length=20), nullable=False),
        sa.Column("address", sa.String(length=320), nullable=False),
        sa.Column("reason", sa.Enum("bounce", "complaint", "unsubscribe", "manual", name="suppressionreason", native_enum=False, length=20), nullable=False),
        sa.Column("detail", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_suppressions"),
        sa.UniqueConstraint("tenant_id", "channel", "address", name="uq_suppressions_tenant_channel_address"),
    )
    op.create_index("ix_suppressions_tenant_id", "suppressions", ["tenant_id"])


def downgrade() -> None:
    op.drop_table("suppressions")
