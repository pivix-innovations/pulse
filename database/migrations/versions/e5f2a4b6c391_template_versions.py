"""template versions: draft/publish with history

Revision ID: e5f2a4b6c391
Revises: d1e3f5a7b280
Create Date: 2026-10-14

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e5f2a4b6c391"
down_revision: Union[str, Sequence[str], None] = "d1e3f5a7b280"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NOW = sa.text("now()")


def upgrade() -> None:
    op.add_column("templates", sa.Column("published_version", sa.Integer(), nullable=True))
    op.create_table(
        "template_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("template_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("subject", sa.String(length=500), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("format", sa.Enum("markdown", "html", "text", name="templateformat", native_enum=False, length=20), nullable=False),
        sa.Column("note", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.ForeignKeyConstraint(["template_id"], ["templates.id"], name="fk_template_versions_template_id_templates", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_template_versions"),
        sa.UniqueConstraint("template_id", "version", name="uq_template_versions_template_version"),
    )
    op.create_index("ix_template_versions_tenant_id", "template_versions", ["tenant_id"])
    # Templates that existed before versioning become version 1, published: nothing changes for anyone.
    op.execute(
        """
        INSERT INTO template_versions (id, tenant_id, template_id, version, subject, body, format, note)
        SELECT gen_random_uuid(), tenant_id, id, 1, subject, body, format, 'Imported when versioning was introduced'
        FROM templates
        """
    )
    op.execute("UPDATE templates SET published_version = 1")


def downgrade() -> None:
    op.drop_table("template_versions")
    op.drop_column("templates", "published_version")
