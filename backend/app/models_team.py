"""团队资源模型 —— 套图模板(MockupTemplate)+ 其下多张产品图(MockupTemplateImage)。

「团队资源」按 `org_id` 共享:同一组织(org)内的成员看到同一批套图模板。现有数据暂统一到 org_id=1
(User 也加了 org_id 默认 1)。一个套图模板 = N 张已印有图案的真实产品照;商品套图运行时,把每张照片
里的原印花自动替换成用户上传的新印花(见 services/mockup_replace.py)。
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def _now() -> datetime:
    return datetime.now(UTC)


class MockupTemplate(Base):
    """套图模板:一组产品照(团队资源,按 org 共享)。"""
    __tablename__ = "mockup_templates"
    __table_args__ = ({"comment": "套图模板表:一组产品照(团队资源,按 org 共享),用于商品套图替换印花"},)
    id: Mapped[int] = mapped_column(primary_key=True, comment="套图模板ID(主键)")
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, comment="创建者用户ID")
    org_id: Mapped[int] = mapped_column(Integer, default=1, index=True,
                                        comment="团队/组织ID(同组共享,当前统一=1)")
    name: Mapped[str] = mapped_column(String(255), comment="模板名")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, comment="创建时间(UTC)")

    images: Mapped[list[MockupTemplateImage]] = relationship(
        back_populates="template", cascade="all, delete-orphan", order_by="MockupTemplateImage.idx")


class MockupTemplateImage(Base):
    """套图模板里的一张产品照。path = /files/{job_id}/{name}(与素材一致的对外 url)。"""
    __tablename__ = "mockup_template_images"
    __table_args__ = ({"comment": "套图模板图表:某套图模板下的一张产品照"},)
    id: Mapped[int] = mapped_column(primary_key=True, comment="模板图ID(主键)")
    template_id: Mapped[int] = mapped_column(ForeignKey("mockup_templates.id", ondelete="CASCADE"), index=True,
                                             comment="所属套图模板ID(FK mockup_templates.id,级联删)")
    path: Mapped[str] = mapped_column(String(512), comment="产品照对外url(/files/{作业ID}/{文件名})")
    idx: Mapped[int] = mapped_column(Integer, default=0, comment="展示顺序")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, comment="创建时间(UTC)")

    template: Mapped[MockupTemplate] = relationship(back_populates="images")
