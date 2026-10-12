"""phase6: delivery lifecycle — dispatch claim + confirmation expectation

Revision ID: a3c9e7b21f04
Revises: f7a2c1d0e5b3
Create Date: 2026-10-12

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "a3c9e7b21f04"
down_revision: Union[str, Sequence[str], None] = "f7a2c1d0e5b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "delivery_attempts",
        sa.Column("dispatch_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "delivery_attempts",
        sa.Column(
            "expects_confirmation", sa.Boolean(), server_default=sa.true(), nullable=False
        ),
    )


def downgrade() -> None:
    op.drop_column("delivery_attempts", "expects_confirmation")
    op.drop_column("delivery_attempts", "dispatch_started_at")
