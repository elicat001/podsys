"""采集路由 — 插件采集回传(ingest)+ 选择工作台(staging)+ 同步入库(sync)。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..auth import current_user
from ..db import get_db
from ..models_db import User
from ..services import collect_tasks as svc

router = APIRouter(prefix="/api/collect-tasks", tags=["collect-tasks"])


class IngestItem(BaseModel):
    url: str = Field(max_length=2048)
    hires_url: str = Field("", max_length=2048)
    title: str = Field("", max_length=512)
    price: str = Field("", max_length=64)
    rating: str = Field("", max_length=32)
    source_url: str = Field("", max_length=2048)
    platform: str = Field("", max_length=32)


class IngestIn(BaseModel):
    source: str = Field("plugin", max_length=64)
    platform: str = Field("", max_length=32)
    items: list[IngestItem] = Field(default_factory=list, max_length=500)


class SyncIn(BaseModel):
    image_ids: list[int] = Field(default_factory=list, max_length=500)


@router.post("/ingest")
def ingest(body: IngestIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """插件采集回传:商品卡(图+标题/价格/评分/链接)→ 暂存(未同步)。采集免费,不扣点。"""
    items = [it.model_dump() for it in body.items]
    if not items:
        raise HTTPException(status_code=400, detail="items 不能为空")
    task = svc.ingest(db, owner_id=user.id, items=items, source=body.source, platform_hint=body.platform)
    # filtered = 收到但被「非商品图」过滤掉的张数(商标/雪碧图/UI),前端据此提示用户「已自动过滤」
    return {"task_id": task.id, "count": task.count, "filtered": max(0, len(items) - task.count), "status": "collected"}


@router.get("/staging")
def staging(platform: str | None = None, user: User = Depends(current_user),
            db: Session = Depends(get_db)):
    """选择工作台:本人未同步的暂存采集图。"""
    return {"items": svc.list_staging(db, owner_id=user.id, platform=platform)}


@router.delete("/staging")
def delete_staging(body: SyncIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """删除选中的暂存项。"""
    n = svc.delete_staging(db, owner_id=user.id, image_ids=body.image_ids)
    return {"deleted": n}


@router.post("/sync")
def sync(body: SyncIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """同步:对选中暂存项服务端取图入库(此时存储才增长),并标侵权风险。"""
    if not body.image_ids:
        raise HTTPException(status_code=400, detail="image_ids 不能为空")
    return svc.sync_images(db, owner_id=user.id, image_ids=body.image_ids)
