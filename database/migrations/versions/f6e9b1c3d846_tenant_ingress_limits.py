"""per-tenant ingress limits (rate, burst, daily quota); NULL means the platform default

Revision ID: f6e9b1c3d846
Revises: e4c7f9a1b624
Create Date: 2026-10-13

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f6e9b1c3d846"
down_revision: Union[str, Sequence[str], None] = "e4c7f9a1b624"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("tenants", sa.Column("rate_limit_per_second", sa.Float(), nullable=True))
    op.add_column("tenants", sa.Column("rate_burst", sa.Integer(), nullable=True))
    op.add_column("tenants", sa.Column("daily_quota", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("tenants", "daily_quota")
    op.drop_column("tenants", "rate_burst")
    op.drop_column("tenants", "rate_limit_per_second")
