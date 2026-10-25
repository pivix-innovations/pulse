from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from database.models.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin


class DigestBatchModel(Base, UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin):
    __tablename__ = "digest_batches"

    workflow_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("workflows.id"), nullable=False)
    recipient_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("recipients.id"), nullable=False)
    digest_key: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    anchor_notification_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("notifications.id"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open")
    item_count: Mapped[int] = mapped_column(nullable=False, default=0)
    flush_at: Mapped[datetime] = mapped_column(nullable=False)
    flushed_at: Mapped[datetime | None] = mapped_column(nullable=True)

    __table_args__ = (
        # At most one open batch per recipient, workflow and key: new events join it.
        Index(
            "uq_digest_batches_open", "workflow_id", "recipient_id", "digest_key",
            unique=True, postgresql_where="status = 'open'",
        ),
        Index("ix_digest_batches_due", "flush_at", postgresql_where="status = 'open'"),
    )


class DigestItemModel(Base, UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin):
    __tablename__ = "digest_items"

    batch_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("digest_batches.id"), nullable=False)
    notification_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("notifications.id"), nullable=False
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    __table_args__ = (
        Index("ix_digest_items_batch", "batch_id", "created_at"),
        Index("ix_digest_items_notification", "notification_id"),
    )
