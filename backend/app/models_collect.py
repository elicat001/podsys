"""ORM models for collection tasks — 采集任务与采集图持久化。

新增独立模块,`import` 即把表注册到 `Base.metadata`。
Tech Lead 需在 `db.init_db()` 里 `import models_collect` 以触发 `create_all`。
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def _now() -> datetime:
    return datetime.now(UTC)


class CollectionTask(Base):
    """一次采集动作产生的任务,聚合若干采集图。"""
    __tablename__ = "collection_tasks"
    __table_args__ = ({"comment": "采集任务表:一次采集动作(聚合若干采集图)"},)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, comment="采集任务ID(随机hex)")
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, comment="归属用户ID")
    source: Mapped[str] = mapped_column(String(64), default="plugin", comment="采集来源(默认 plugin 浏览器插件)")
    status: Mapped[str] = mapped_column(String(16), default="collected", comment="状态:collected已采集等")
    count: Mapped[int] = mapped_column(Integer, default=0, comment="本次采集图片数")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, comment="创建时间(UTC)")

    images: Mapped[list[CollectedImage]] = relationship(
        back_populates="task", cascade="all, delete-orphan"
    )


class CollectedImage(Base):
    """采集记录:暂存(synced=False)= 选择工作台;入库(synced=True)= 找图库的富记录
    (图 + 标题/价格/评分/来源链接 + 同步后的 asset 直链)。"""
    __tablename__ = "collected_images"
    # 采集箱/找图都是 join 后 `WHERE task_id 属本人 AND synced=?` → 复合索引
    __table_args__ = (
        Index("ix_collected_task_synced", "task_id", "synced"),
        {"comment": "采集图表:暂存(synced=0)+ 同步入库后的富记录(图+标题/价格/评分/来源链接)"},
    )
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True, comment="采集图ID(主键)")
    task_id: Mapped[str] = mapped_column(
        ForeignKey("collection_tasks.id"), index=True, comment="所属采集任务ID(FK collection_tasks.id)"
    )
    url: Mapped[str] = mapped_column(String(1024), comment="原图URL(采集到的图片地址)")
    hires_url: Mapped[str] = mapped_column(String(1024), comment="高清图URL(若有)")
    platform: Mapped[str] = mapped_column(String(32), comment="来源平台:temu/amazon 等")
    title: Mapped[str] = mapped_column(String(255), default="", comment="商品标题")
    selected: Mapped[bool] = mapped_column(Boolean, default=False, comment="工作台是否勾选")
    price: Mapped[str] = mapped_column(String(32), default="", comment="商品价格(文本)")
    rating: Mapped[str] = mapped_column(String(16), default="", comment="商品评分(文本)")
    source_url: Mapped[str] = mapped_column(String(1024), default="", comment="商品详情页URL")
    synced: Mapped[bool] = mapped_column(Boolean, default=False, index=True,
                                         comment="是否已同步入库(true=已取图存为 Asset 进找图库)")
    synced_asset_id: Mapped[int | None] = mapped_column(Integer, nullable=True,
                                                        comment="同步后对应的素材ID(Asset.id)")
    asset_url: Mapped[str] = mapped_column(String(1024), default="", comment="同步后素材的对外直链(/files/...)")

    task: Mapped[CollectionTask] = relationship(back_populates="images")
