from __future__ import annotations

import uuid

from sqlalchemy import Boolean, Enum, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from database.models.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin
from domain.enums import ChannelType, TemplateFormat

_channel_enum = Enum(
    ChannelType, values_callable=lambda e: [m.value for m in e], native_enum=False, length=20
)


class TemplateModel(Base, UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin):
    __tablename__ = "templates"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    channel: Mapped[ChannelType] = mapped_column(_channel_enum, nullable=False)
    subject: Mapped[str | None] = mapped_column(String(500), nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    format: Mapped[TemplateFormat] = mapped_column(
        Enum(TemplateFormat, values_callable=lambda e: [m.value for m in e], native_enum=False, length=20),
        nullable=False,
        default=TemplateFormat.MARKDOWN,
    )
    locale: Mapped[str] = mapped_column(String(16), nullable=False, default="en")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    published_version: Mapped[int | None] = mapped_column(Integer, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "name", "channel", "locale", name="uq_templates_tenant_name_channel_locale"
        ),
    )


class TemplateVersionModel(Base, UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin):
    __tablename__ = "template_versions"

    template_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("templates.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    subject: Mapped[str | None] = mapped_column(String(500), nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    format: Mapped[TemplateFormat] = mapped_column(
        Enum(TemplateFormat, values_callable=lambda e: [m.value for m in e], native_enum=False, length=20),
        nullable=False,
    )
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)

    __table_args__ = (UniqueConstraint("template_id", "version", name="uq_template_versions_template_version"),)
