"""provider catalog: label + encrypted secrets, multiple instances per type

Revision ID: c1a5d7e9f402
Revises: b8d4f6a2c913
Create Date: 2026-10-13

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c1a5d7e9f402"
down_revision: Union[str, Sequence[str], None] = "b8d4f6a2c913"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("providers", sa.Column("label", sa.String(200), nullable=False, server_default=""))
    op.add_column("providers", sa.Column("secrets_encrypted", sa.Text(), nullable=True))
    op.execute("UPDATE providers SET label = name WHERE label = ''")
    op.alter_column("providers", "label", server_default=None)
    op.drop_constraint("uq_providers_tenant_channel_name", "providers", type_="unique")
    op.create_unique_constraint("uq_providers_tenant_label", "providers", ["tenant_id", "label"])


def downgrade() -> None:
    op.drop_constraint("uq_providers_tenant_label", "providers", type_="unique")
    op.create_unique_constraint(
        "uq_providers_tenant_channel_name", "providers", ["tenant_id", "channel", "name"]
    )
    op.drop_column("providers", "secrets_encrypted")
    op.drop_column("providers", "label")
