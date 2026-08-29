from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Enum, ForeignKey, Index
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from database.models.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin
from domain.enums import NotificationStatus


class NotificationModel(Base, UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin):
    __tablename__ = "notifications"

    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("events.id"), nullable=False
    )
    workflow_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workflows.id"), nullable=False
    )
    status: Mapped[NotificationStatus] = mapped_column(
        Enum(NotificationStatus, values_callable=lambda e: [m.value for m in e], native_enum=False, length=20),
        nullable=False,
        default=NotificationStatus.CREATED,
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    scheduled_at: Mapped[datetime | None] = mapped_column(nullable=True)

    __table_args__ = (
        Index("ix_notifications_tenant_status", "tenant_id", "status"),
        # For the scheduler's due-notification poll.
        Index("ix_notifications_status_scheduled_at", "status", "scheduled_at"),
    )
