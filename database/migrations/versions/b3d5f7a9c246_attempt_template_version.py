"""delivery attempts record the template version they were rendered from

Revision ID: b3d5f7a9c246
Revises: a2c4e6b8d135
Create Date: 2026-10-14

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b3d5f7a9c246"
down_revision: Union[str, Sequence[str], None] = "a2c4e6b8d135"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("delivery_attempts", sa.Column("template_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("delivery_attempts", sa.Column("template_version", sa.Integer(), nullable=True))
    op.create_index(
        "ix_delivery_attempts_template", "delivery_attempts", ["tenant_id", "template_id", "template_version"],
        postgresql_where=sa.text("template_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_delivery_attempts_template", table_name="delivery_attempts")
    op.drop_column("delivery_attempts", "template_version")
    op.drop_column("delivery_attempts", "template_id")
