"""店铺模型:Shop —— 用户在各平台开的店铺,按店铺上架。"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def _now() -> datetime:
    return datetime.now(UTC)


class Shop(Base):
    """用户店铺:某平台下的一个店铺,可向其按店铺上架商品。"""
    __tablename__ = "shops"
    __table_args__ = ({"comment": "店铺表:用户在各平台开的店铺,按店铺上架商品"},)
    id: Mapped[int] = mapped_column(primary_key=True, comment="店铺ID(主键)")
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, comment="归属用户ID")
    platform: Mapped[str] = mapped_column(String(32), comment="平台:temu / tiktok / etsy / local")
    name: Mapped[str] = mapped_column(String(255), comment="店铺名")
    status: Mapped[str] = mapped_column(String(16), default="active", comment="状态:active启用 等")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, comment="创建时间(UTC)")
