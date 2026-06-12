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
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    source: Mapped[str] = mapped_column(String(64), default="plugin")
    status: Mapped[str] = mapped_column(String(16), default="collected")
    count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    images: Mapped[list[CollectedImage]] = relationship(
        back_populates="task", cascade="all, delete-orphan"
    )


class CollectedImage(Base):
    """采集记录:暂存(synced=False)= 选择工作台;入库(synced=True)= 找图库的富记录
    (图 + 标题/价格/评分/来源链接 + 同步后的 asset 直链)。"""
    __tablename__ = "collected_images"
    # 采集箱/找图都是 join 后 `WHERE task_id 属本人 AND synced=?` → 复合索引
    __table_args__ = (Index("ix_collected_task_synced", "task_id", "synced"),)
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(
        ForeignKey("collection_tasks.id"), index=True
    )
    url: Mapped[str] = mapped_column(String(1024))
    hires_url: Mapped[str] = mapped_column(String(1024))
    platform: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(255), default="")
    selected: Mapped[bool] = mapped_column(Boolean, default=False)
    # batch13:采集→选择→同步。暂存只存元数据 + URL(零存储),同步时服务端取图入库。
    price: Mapped[str] = mapped_column(String(32), default="")
    rating: Mapped[str] = mapped_column(String(16), default="")
    source_url: Mapped[str] = mapped_column(String(1024), default="")
    synced: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    synced_asset_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    asset_url: Mapped[str] = mapped_column(String(1024), default="")

    task: Mapped[CollectionTask] = relationship(back_populates="images")
