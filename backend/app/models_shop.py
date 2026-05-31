"""店铺模型:Shop —— 用户在各平台开的店铺,按店铺上架。"""
from __future__ import annotations
from datetime import datetime, timezone
from sqlalchemy import String, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from .db import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Shop(Base):
    """用户店铺:某平台下的一个店铺,可向其按店铺上架商品。"""
    __tablename__ = "shops"
    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    platform: Mapped[str] = mapped_column(String(32))   # temu|tiktok|etsy|local
    name: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(16), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
