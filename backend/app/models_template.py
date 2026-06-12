"""模板模型(batch10):刊登模板 ListingTemplate + 导出模板 ExportTemplate。"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def _now() -> datetime:
    return datetime.now(UTC)


class ListingTemplate(Base):
    """刊登模板:某平台下一套可复用的刊登字段(标题/描述/属性等)。"""
    __tablename__ = "listing_templates"
    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    platform: Mapped[str] = mapped_column(String(32), default="")
    fields: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class ExportTemplate(Base):
    """导出模板:印花/套图导出规格(分辨率、画布尺寸、格式)。"""
    __tablename__ = "export_templates"
    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    dpi: Mapped[int] = mapped_column(Integer, default=300)
    width_cm: Mapped[float] = mapped_column(Float, default=30.0)
    height_cm: Mapped[float] = mapped_column(Float, default=40.0)
    fmt: Mapped[str] = mapped_column(String(16), default="png")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
