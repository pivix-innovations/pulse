from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from database.models.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin


class InboxMessageModel(Base, UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin):
    __tablename__ = "inbox_messages"

    recipient_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("recipients.id"), nullable=False
    )
    notification_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("notifications.id"), nullable=False
    )
    attempt_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("delivery_attempts.id"), nullable=False
    )
    title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    read_at: Mapped[datetime | None] = mapped_column(nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(nullable=True)

    __table_args__ = (
        # One message per attempt: dispatch retries must not duplicate it.
        UniqueConstraint("attempt_id", name="uq_inbox_messages_attempt_id"),
        # The list query: a recipient's messages newest first.
        Index("ix_inbox_messages_recipient_created", "tenant_id", "recipient_id", "created_at", "id"),
        # Unread badge counts.
        Index(
            "ix_inbox_messages_unread", "tenant_id", "recipient_id",
            postgresql_where="read_at IS NULL AND archived_at IS NULL",
        ),
    )
