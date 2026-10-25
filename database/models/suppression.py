from __future__ import annotations

from sqlalchemy import Enum, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from database.models.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin
from domain.entities.suppression import SuppressionReason
from domain.enums import ChannelType


class SuppressionModel(Base, UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin):
    __tablename__ = "suppressions"

    channel: Mapped[ChannelType] = mapped_column(
        Enum(ChannelType, values_callable=lambda e: [m.value for m in e], native_enum=False, length=20),
        nullable=False,
    )
    address: Mapped[str] = mapped_column(String(320), nullable=False)
    reason: Mapped[SuppressionReason] = mapped_column(
        Enum(SuppressionReason, values_callable=lambda e: [m.value for m in e], native_enum=False, length=20),
        nullable=False,
    )
    detail: Mapped[str | None] = mapped_column(String(500), nullable=True)

    __table_args__ = (
        UniqueConstraint("tenant_id", "channel", "address", name="uq_suppressions_tenant_channel_address"),
    )
