"""ORM models: User, Asset, Job, Product, Listing."""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def _now() -> datetime:
    return datetime.now(UTC)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    credits: Mapped[int] = mapped_column(Integer, default=100)   # 简易计费:点数
    org_id: Mapped[int] = mapped_column(Integer, default=1, index=True)  # 团队资源共享维度(暂统一=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    assets: Mapped[list[Asset]] = relationship(back_populates="owner")
    products: Mapped[list[Product]] = relationship(back_populates="owner")


class Asset(Base):
    """素材库条目(原图/印花),带感知哈希用于侵权/查重。"""
    __tablename__ = "assets"
    # 素材库列表/配额几乎都是 `WHERE owner_id AND deleted=?` → 复合索引覆盖
    __table_args__ = (Index("ix_assets_owner_deleted", "owner_id", "deleted"),)
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

    owner: Mapped[User] = relationship(back_populates="assets")


class Job(Base):
    """异步/可追溯作业记录。"""
    __tablename__ = "jobs"
    # 最热的表:任务中心列表/最近任务高频轮询 = `WHERE owner_id ORDER BY created_at DESC`;
    # reap 自愈 = `WHERE status IN (pending,running)`。两条索引把这俩查询从全表扫降到走索引。
    __table_args__ = (
        Index("ix_jobs_owner_created", "owner_id", "created_at"),
        Index("ix_jobs_status", "status"),
    )
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    kind: Mapped[str] = mapped_column(String(32))     # process|generate|edit|split|publish
    # 前端工具 id(tools.js),用于「我的空间·任务中心」按 大模块→小模块 分组与展示。
    # 与 kind 分离:有的工具共用同一后端 ep(如「一键抠图」用 process),靠 tool_id 区分。
    tool_id: Mapped[str] = mapped_column(String(32), default="")
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|running|done|error
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)   # 开始执行
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # 结束(done/error)


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

    owner: Mapped[User] = relationship(back_populates="products")
    listings: Mapped[list[Listing]] = relationship(back_populates="product")


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

    product: Mapped[Product] = relationship(back_populates="listings")
