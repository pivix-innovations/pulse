from __future__ import annotations

import uuid

from sqlalchemy import Enum, ForeignKey, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from database.models.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin
from domain.enums import ChannelType


class DeadLetterModel(Base, UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin):
    __tablename__ = "dead_letters"

    attempt_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("delivery_attempts.id"), nullable=False, index=True
    )
    notification_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("notifications.id"), nullable=False
    )
    channel: Mapped[ChannelType] = mapped_column(
        Enum(ChannelType, values_callable=lambda e: [m.value for m in e], native_enum=False, length=20),
        nullable=False,
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
