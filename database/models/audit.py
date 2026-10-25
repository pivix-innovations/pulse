from __future__ import annotations

from typing import Any

from sqlalchemy import Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from database.models.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin


class AuditLogModel(Base, UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin):
    __tablename__ = "audit_log"

    actor_type: Mapped[str] = mapped_column(String(20), nullable=False)
    actor_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    actor_label: Mapped[str | None] = mapped_column(String(320), nullable=True)
    action: Mapped[str] = mapped_column(String(60), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(40), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    __table_args__ = (Index("ix_audit_log_tenant_created", "tenant_id", "created_at"),)
