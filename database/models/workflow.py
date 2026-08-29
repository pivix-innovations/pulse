from __future__ import annotations

from typing import Any

from sqlalchemy import Boolean, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from database.models.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin


class WorkflowModel(Base, UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin):
    __tablename__ = "workflows"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    event_type: Mapped[str] = mapped_column(String(200), nullable=False)
    definition: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (
        UniqueConstraint("tenant_id", "event_type", "name", name="uq_workflows_tenant_event_name"),
    )
