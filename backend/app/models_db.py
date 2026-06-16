"""ORM models: User, Asset, Job, Product, Listing.

每列都带 DB 级 `comment=`(落到 MySQL 的 COLUMN COMMENT,DBeaver / `SHOW FULL COLUMNS` 可见),
方便运维/DBA 直接看懂表结构。改这些注释或加索引属于"改存量表",**必须走 Alembic 迁移**(见 CLAUDE.md)。
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def _now() -> datetime:
    return datetime.now(UTC)


class User(Base):
    __tablename__ = "users"
    __table_args__ = ({"comment": "用户表:账号、密码、计费点数、团队归属"},)
    id: Mapped[int] = mapped_column(primary_key=True, comment="用户ID(主键)")
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, comment="登录邮箱(唯一)")
    password_hash: Mapped[str] = mapped_column(String(255), comment="密码哈希(pbkdf2,不存明文)")
    credits: Mapped[int] = mapped_column(Integer, default=100, comment="计费点数余额(作图按操作扣点)")
    org_id: Mapped[int] = mapped_column(Integer, default=1, index=True,
                                        comment="团队/组织ID(团队资源共享维度,当前统一=1)")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, comment="注册时间(UTC)")

    assets: Mapped[list[Asset]] = relationship(back_populates="owner")
    products: Mapped[list[Product]] = relationship(back_populates="owner")


class Asset(Base):
    """素材库条目(原图/印花/视频),带感知哈希用于侵权/查重。"""
    __tablename__ = "assets"
    # 复合索引:① 列表/配额几乎都 `WHERE owner_id AND deleted=?`;② 按时间排序/筛选 `WHERE owner_id ORDER BY created_at`
    __table_args__ = (
        Index("ix_assets_owner_deleted", "owner_id", "deleted"),
        Index("ix_assets_owner_created", "owner_id", "created_at"),
        {"comment": "素材表:每个上传/生成的图片·视频·文件一行;我的空间、存储配额、回收站的数据源"},
    )
    id: Mapped[int] = mapped_column(primary_key=True, comment="素材ID(主键)")
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, comment="归属用户ID(谁的)")
    name: Mapped[str] = mapped_column(String(255), comment="素材名称(展示用,如『图生视频 通用』)")
    path: Mapped[str] = mapped_column(
        String(512),
        comment="文件位置 /files/{作业ID}/{文件名};对应对象存储 images/ 或 videos/ 下的对象")
    dhash: Mapped[str] = mapped_column(String(32), index=True, comment="感知哈希-结构(亮度梯度),查重/侵权用")
    chash: Mapped[str] = mapped_column(String(128), index=True, comment="感知哈希-颜色签名(4x4 RGB)")
    source: Mapped[str] = mapped_column(String(64), default="upload",
                                        comment="来源:upload上传 / collected采集 / generated生成")
    risk: Mapped[str] = mapped_column(String(16), default="unknown",
                                      comment="侵权风险:safe安全 / review待复核 / high高危 / unknown未知")
    deleted: Mapped[bool] = mapped_column(default=False, index=True,
                                          comment="是否在回收站(软删,true=已移入回收站)")
    batch: Mapped[str] = mapped_column(String(64), default="", index=True, comment="批次标识(批量操作分组)")
    tags: Mapped[list] = mapped_column(JSON, default=list, comment="标签(JSON 字符串数组)")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0,
                                            comment="文件大小(字节);存储配额按此列加总")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, comment="创建时间(UTC),时间筛选/排序依据")

    owner: Mapped[User] = relationship(back_populates="assets")


class Job(Base):
    """异步/可追溯作业记录。状态与结果的唯一真相源(前端轮询它)。"""
    __tablename__ = "jobs"
    # ① 任务中心列表/最近任务高频轮询 = `WHERE owner_id ORDER BY created_at DESC`;② reap 自愈 = `WHERE status IN (...)`
    __table_args__ = (
        Index("ix_jobs_owner_created", "owner_id", "created_at"),
        Index("ix_jobs_status", "status"),
        {"comment": "异步作业表:状态/结果的唯一真相源,前端轮询 /api/jobs/{id};作业ID 也是产物文件夹名"},
    )
    id: Mapped[str] = mapped_column(String(32), primary_key=True,
                                    comment="作业ID(随机12位hex,同时作为产物目录 outputs/{id} 与对象 key 的一段)")
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True,
                                                 comment="归属用户ID(可空=系统作业)")
    kind: Mapped[str] = mapped_column(String(32),
                                      comment="作业类型:process抠图/generate文生图/edit改图/aivideo图生视频 等")
    tool_id: Mapped[str] = mapped_column(String(32), default="",
                                         comment="前端工具ID(tools.js);用于我的空间按工具分组展示")
    status: Mapped[str] = mapped_column(String(16), default="pending",
                                        comment="状态:pending排队 / running执行中 / done完成 / error失败")
    params: Mapped[dict] = mapped_column(JSON, default=dict, comment="作业入参(JSON)")
    result: Mapped[dict] = mapped_column(JSON, default=dict, comment="作业结果(JSON,含产物 *_url)")
    error: Mapped[str] = mapped_column(Text, default="", comment="失败信息(异常类型+消息)")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, comment="创建(入队)时间(UTC)")
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, comment="开始执行时间(UTC)")
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True,
                                                         comment="结束时间(done/error,UTC)")


class Product(Base):
    """商品库条目(可上架的商品)。"""
    __tablename__ = "products"
    __table_args__ = ({"comment": "商品库表:可上架的商品条目(印花+套图+属性)"},)
    id: Mapped[int] = mapped_column(primary_key=True, comment="商品ID(主键)")
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, comment="归属用户ID")
    title: Mapped[str] = mapped_column(String(255), comment="商品标题")
    category: Mapped[str] = mapped_column(String(64), default="apparel", comment="品类(默认 apparel 服装)")
    price: Mapped[float] = mapped_column(Float, default=19.99, comment="售价")
    print_path: Mapped[str] = mapped_column(String(512), default="", comment="印花文件位置(/files/...)")
    mockup_path: Mapped[str] = mapped_column(String(512), default="", comment="套图预览文件位置(/files/...)")
    attrs: Mapped[dict] = mapped_column(JSON, default=dict, comment="扩展属性(JSON,颜色/尺码等)")
    sku: Mapped[str] = mapped_column(String(64), default="", index=True, comment="SKU 货号")
    batch: Mapped[str] = mapped_column(String(64), default="", index=True, comment="批次标识")
    source: Mapped[str] = mapped_column(String(64), default="manual", comment="来源:manual手建 / 其它")
    risk: Mapped[str] = mapped_column(String(16), default="unknown", comment="侵权风险:safe/review/high/unknown")
    tags: Mapped[list] = mapped_column(JSON, default=list, comment="标签(JSON 数组)")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, comment="创建时间(UTC)")

    owner: Mapped[User] = relationship(back_populates="products")
    listings: Mapped[list[Listing]] = relationship(back_populates="product")


class Listing(Base):
    """某商品在某平台的上架记录。"""
    __tablename__ = "listings"
    __table_args__ = ({"comment": "上架记录表:某商品在某平台(temu/tiktok/etsy…)的上架状态"},)
    id: Mapped[int] = mapped_column(primary_key=True, comment="上架记录ID(主键)")
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True, comment="商品ID(FK products.id)")
    platform: Mapped[str] = mapped_column(String(32), comment="平台:temu / tiktok / etsy / local")
    status: Mapped[str] = mapped_column(String(16), default="draft",
                                        comment="上架状态:draft草稿 / published已上架 / failed失败")
    external_id: Mapped[str] = mapped_column(String(128), default="", comment="平台侧商品ID(上架成功后回填)")
    payload: Mapped[dict] = mapped_column(JSON, default=dict, comment="上架请求/回执载荷(JSON)")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, comment="创建时间(UTC)")

    product: Mapped[Product] = relationship(back_populates="listings")
