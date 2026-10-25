"""api key scopes, expiry, last use and rotation lineage

Revision ID: a7f0c2d4e957
Revises: f6e9b1c3d846
Create Date: 2026-10-13

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a7f0c2d4e957"
down_revision: Union[str, Sequence[str], None] = "f6e9b1c3d846"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Existing keys keep working exactly as before: they hold every scope.
    op.add_column(
        "api_keys",
        sa.Column(
            "scopes", postgresql.JSONB(), nullable=False,
            server_default=sa.text("""'["ingest", "read", "client", "admin"]'::jsonb"""),
        ),
    )
    op.alter_column("api_keys", "scopes", server_default=None)
    op.add_column("api_keys", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("api_keys", sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("api_keys", sa.Column("rotated_from_id", postgresql.UUID(as_uuid=True), nullable=True))


def downgrade() -> None:
    op.drop_column("api_keys", "rotated_from_id")
    op.drop_column("api_keys", "last_used_at")
    op.drop_column("api_keys", "expires_at")
    op.drop_column("api_keys", "scopes")
