from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Enum, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from database.models.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin
from domain.enums import DevicePlatform


class DeviceTokenModel(Base, UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin):
    __tablename__ = "device_tokens"

    recipient_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("recipients.id"), nullable=False
    )
    platform: Mapped[DevicePlatform] = mapped_column(
        Enum(DevicePlatform, values_callable=lambda e: [m.value for m in e], native_enum=False, length=20),
        nullable=False,
    )
    token: Mapped[str] = mapped_column(String(4096), nullable=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(nullable=True)

    __table_args__ = (
        UniqueConstraint("tenant_id", "token", name="uq_device_tokens_tenant_token"),
        Index("ix_device_tokens_tenant_recipient", "tenant_id", "recipient_id"),
    )
