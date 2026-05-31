"""ORM models for collection tasks — 采集任务与采集图持久化。

新增独立模块,`import` 即把表注册到 `Base.metadata`。
Tech Lead 需在 `db.init_db()` 里 `import models_collect` 以触发 `create_all`。
"""
from __future__ import annotations
from datetime import datetime, timezone
from sqlalchemy import String, Integer, Boolean, ForeignKey, DateTime
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .db import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class CollectionTask(Base):
    """一次采集动作产生的任务,聚合若干采集图。"""
    __tablename__ = "collection_tasks"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    source: Mapped[str] = mapped_column(String(64), default="plugin")
    status: Mapped[str] = mapped_column(String(16), default="collected")
    count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    images: Mapped[list["CollectedImage"]] = relationship(
        back_populates="task", cascade="all, delete-orphan"
    )


class CollectedImage(Base):
    """采集任务下的单张图,带平台识别与高清升级后的地址。"""
    __tablename__ = "collected_images"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(
        ForeignKey("collection_tasks.id"), index=True
    )
    url: Mapped[str] = mapped_column(String(1024))
    hires_url: Mapped[str] = mapped_column(String(1024))
    platform: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(255), default="")
    selected: Mapped[bool] = mapped_column(Boolean, default=False)

    task: Mapped["CollectionTask"] = relationship(back_populates="images")
