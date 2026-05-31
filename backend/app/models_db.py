"""ORM models: User, Asset, Job, Product, Listing."""
from __future__ import annotations
from datetime import datetime, timezone
from sqlalchemy import String, Integer, Float, Text, ForeignKey, DateTime, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .db import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    credits: Mapped[int] = mapped_column(Integer, default=100)   # 简易计费:点数
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    assets: Mapped[list["Asset"]] = relationship(back_populates="owner")
    products: Mapped[list["Product"]] = relationship(back_populates="owner")


class Asset(Base):
    """素材库条目(原图/印花),带感知哈希用于侵权/查重。"""
    __tablename__ = "assets"
    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    path: Mapped[str] = mapped_column(String(512))
    dhash: Mapped[str] = mapped_column(String(32), index=True)   # 结构(亮度梯度)
    chash: Mapped[str] = mapped_column(String(128), index=True)  # 绝对颜色签名(4x4 RGB)
    source: Mapped[str] = mapped_column(String(64), default="upload")  # upload|collected|generated
    risk: Mapped[str] = mapped_column(String(16), default="unknown")   # safe|review|high|unknown
    # batch10:我的空间深度 —— 回收站软删 / 批次 / 标签
    deleted: Mapped[bool] = mapped_column(default=False, index=True)
    batch: Mapped[str] = mapped_column(String(64), default="", index=True)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)        # 用于存储配额统计
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    owner: Mapped["User"] = relationship(back_populates="assets")


class Job(Base):
    """异步/可追溯作业记录。"""
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    kind: Mapped[str] = mapped_column(String(32))     # process|generate|edit|split|publish
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|running|done|error
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Product(Base):
    """商品库条目。"""
    __tablename__ = "products"
    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(255))
    category: Mapped[str] = mapped_column(String(64), default="apparel")
    price: Mapped[float] = mapped_column(Float, default=19.99)
    print_path: Mapped[str] = mapped_column(String(512), default="")
    mockup_path: Mapped[str] = mapped_column(String(512), default="")
    attrs: Mapped[dict] = mapped_column(JSON, default=dict)
    # batch10:商品库管理深度 —— SKU / 批次 / 来源 / 风险 / 标签
    sku: Mapped[str] = mapped_column(String(64), default="", index=True)
    batch: Mapped[str] = mapped_column(String(64), default="", index=True)
    source: Mapped[str] = mapped_column(String(64), default="manual")
    risk: Mapped[str] = mapped_column(String(16), default="unknown")
    tags: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    owner: Mapped["User"] = relationship(back_populates="products")
    listings: Mapped[list["Listing"]] = relationship(back_populates="product")


class Listing(Base):
    """某商品在某平台的上架记录。"""
    __tablename__ = "listings"
    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    platform: Mapped[str] = mapped_column(String(32))   # temu|tiktok|etsy|local
    status: Mapped[str] = mapped_column(String(16), default="draft")  # draft|published|failed
    external_id: Mapped[str] = mapped_column(String(128), default="")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    product: Mapped["Product"] = relationship(back_populates="listings")
