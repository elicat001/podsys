"""用户自定义工作流持久化:SavedWorkflow —— 保存可复用的 step 序列。"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def _now() -> datetime:
    return datetime.now(UTC)


class SavedWorkflow(Base):
    """用户保存的自定义工作流:有序 step 列表 + 参数。"""
    __tablename__ = "saved_workflows"
    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    steps: Mapped[list] = mapped_column(JSON, default=list)   # list[str]
    params: Mapped[dict] = mapped_column(JSON, default=dict)  # dict
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
