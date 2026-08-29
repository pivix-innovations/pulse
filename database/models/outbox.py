from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Enum, Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from database.models.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin
from domain.enums import OutboxStatus


class OutboxEntryModel(Base, UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin):
    __tablename__ = "outbox_entries"

    aggregate_type: Mapped[str] = mapped_column(String(100), nullable=False)
    aggregate_id: Mapped[str] = mapped_column(String(64), nullable=False)
    topic: Mapped[str] = mapped_column(String(200), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[OutboxStatus] = mapped_column(
        Enum(OutboxStatus, values_callable=lambda e: [m.value for m in e], native_enum=False, length=20),
        nullable=False,
        default=OutboxStatus.PENDING,
    )
    published_at: Mapped[datetime | None] = mapped_column(nullable=True)

    __table_args__ = (
        # The relay polls oldest pending entries first.
        Index("ix_outbox_entries_status_created_at", "status", "created_at"),
    )
