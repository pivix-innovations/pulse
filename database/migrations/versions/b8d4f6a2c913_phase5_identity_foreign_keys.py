"""phase5: identity foreign keys

Revision ID: b8d4f6a2c913
Revises: a3c9e7b21f04
Create Date: 2026-10-12

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b8d4f6a2c913"
down_revision: Union[str, Sequence[str], None] = "a3c9e7b21f04"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (table, column, referred table, constraint name)
_FKS = [
    ("tenant_memberships", "tenant_id", "tenants", "fk_tenant_memberships_tenant_id_tenants"),
    ("tenant_memberships", "user_id", "users", "fk_tenant_memberships_user_id_users"),
    ("api_keys", "tenant_id", "tenants", "fk_api_keys_tenant_id_tenants"),
    ("sessions", "user_id", "users", "fk_sessions_user_id_users"),
]


def upgrade() -> None:
    # Drop pre-existing orphans so the constraints can be added.
    for table, column, ref, _ in _FKS:
        op.execute(f"DELETE FROM {table} WHERE {column} NOT IN (SELECT id FROM {ref})")
    for table, column, ref, name in _FKS:
        op.create_foreign_key(name, table, ref, [column], ["id"], ondelete="CASCADE")


def downgrade() -> None:
    for table, _, _, name in reversed(_FKS):
        op.drop_constraint(name, table, type_="foreignkey")
