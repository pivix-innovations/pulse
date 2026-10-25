"""delivery attempts remember the provider's message id (for delivery receipts)

Revision ID: d2b6e8f0a513
Revises: c1a5d7e9f402
Create Date: 2026-10-13

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d2b6e8f0a513"
down_revision: Union[str, Sequence[str], None] = "c1a5d7e9f402"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("delivery_attempts", sa.Column("provider_message_id", sa.String(255), nullable=True))
    op.create_index(
        "ix_delivery_attempts_provider_message_id",
        "delivery_attempts",
        ["tenant_id", "provider_message_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_delivery_attempts_provider_message_id", table_name="delivery_attempts")
    op.drop_column("delivery_attempts", "provider_message_id")
