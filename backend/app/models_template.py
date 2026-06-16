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
    __table_args__ = ({"comment": "刊登模板表:某平台一套可复用的刊登字段(标题/描述/属性)"},)
    id: Mapped[int] = mapped_column(primary_key=True, comment="刊登模板ID(主键)")
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, comment="归属用户ID")
    name: Mapped[str] = mapped_column(String(255), comment="模板名")
    platform: Mapped[str] = mapped_column(String(32), default="", comment="适用平台")
    fields: Mapped[dict] = mapped_column(JSON, default=dict, comment="刊登字段(JSON:标题/描述/属性等)")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, comment="创建时间(UTC)")


class ExportTemplate(Base):
    """导出模板:印花/套图导出规格(分辨率、画布尺寸、格式)。"""
    __tablename__ = "export_templates"
    __table_args__ = ({"comment": "导出模板表:印花/套图导出规格(分辨率、画布尺寸、格式)"},)
    id: Mapped[int] = mapped_column(primary_key=True, comment="导出模板ID(主键)")
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, comment="归属用户ID")
    name: Mapped[str] = mapped_column(String(255), comment="模板名")
    dpi: Mapped[int] = mapped_column(Integer, default=300, comment="分辨率(DPI,默认300)")
    width_cm: Mapped[float] = mapped_column(Float, default=30.0, comment="画布宽(厘米)")
    height_cm: Mapped[float] = mapped_column(Float, default=40.0, comment="画布高(厘米)")
    fmt: Mapped[str] = mapped_column(String(16), default="png", comment="导出格式:png/jpg/tiff/pdf 等")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, comment="创建时间(UTC)")
