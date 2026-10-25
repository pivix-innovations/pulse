"""delivery attempts keep the trace context they were created under

Revision ID: c4e6a8b0d357
Revises: b3d5f7a9c246
Create Date: 2026-10-14

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c4e6a8b0d357"
down_revision: Union[str, Sequence[str], None] = "b3d5f7a9c246"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("delivery_attempts", sa.Column("traceparent", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("delivery_attempts", "traceparent")
