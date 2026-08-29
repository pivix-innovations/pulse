from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Enum, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from database.models.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin
from domain.enums import ChannelType, DeliveryStatus


class DeliveryAttemptModel(Base, UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin):
    __tablename__ = "delivery_attempts"

    notification_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("notifications.id"), nullable=False
    )
    recipient_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("recipients.id"), nullable=False
    )
    channel: Mapped[ChannelType] = mapped_column(
        Enum(ChannelType, values_callable=lambda e: [m.value for m in e], native_enum=False, length=20),
        nullable=False,
    )
    status: Mapped[DeliveryStatus] = mapped_column(
        Enum(DeliveryStatus, values_callable=lambda e: [m.value for m in e], native_enum=False, length=20),
        nullable=False,
        default=DeliveryStatus.PENDING,
    )
    provider_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("providers.id"), nullable=True
    )
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    subject: Mapped[str | None] = mapped_column(String(500), nullable=True)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    body_html: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(nullable=True)
    next_retry_at: Mapped[datetime | None] = mapped_column(nullable=True)

    __table_args__ = (
        # One attempt row per (notification x recipient x channel); this is
        # also the idempotency boundary for dispatch.
        UniqueConstraint(
            "notification_id",
            "recipient_id",
            "channel",
            name="uq_delivery_attempts_notification_recipient_channel",
        ),
        # For the reconciliation sweep: stuck-in-sent lookups per channel.
        Index("ix_delivery_attempts_status_channel_sent_at", "status", "channel", "sent_at"),
        Index("ix_delivery_attempts_tenant_notification", "tenant_id", "notification_id"),
    )
