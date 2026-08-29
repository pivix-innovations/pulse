from __future__ import annotations

from sqlalchemy import String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from database.models.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin


class RecipientModel(Base, UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin):
    __tablename__ = "recipients"

    external_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    locale: Mapped[str] = mapped_column(String(16), nullable=False, default="en")
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC")

    __table_args__ = (
        UniqueConstraint("tenant_id", "external_ref", name="uq_recipients_tenant_external_ref"),
    )
