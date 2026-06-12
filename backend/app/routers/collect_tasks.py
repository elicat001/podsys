"""采集路由 — 插件采集回传(ingest)+ 选择工作台(staging)+ 同步入库(sync)。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..auth import current_user
from ..config import settings
from ..db import get_db
from ..models_db import User
from ..services import collect_tasks as svc
from ..services.jobs import create_job
from ..tasks import run_tool
from ..web_utils import enqueue_or_refund

router = APIRouter(prefix="/api/collect-tasks", tags=["collect-tasks"])


def _worker_online() -> bool:
    """是否有 Celery worker 在线能消费任务。用于决定:有 worker → 异步后台跑;
    没 worker(本地只起了后端 / broker 挂了)→ 就地同步执行,**保证一定能跑通,不会卡 pending**。"""
    if settings.celery_eager:
        return True  # 测试/eager:.delay 本就同进程同步执行,视为在线
    try:
        from ..celery_app import celery_app
        return bool(celery_app.control.ping(timeout=0.6))
    except Exception:  # noqa: BLE001 — broker 不可用等一律按「无 worker」降级到同步
        return False


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


class SyncGroup(BaseModel):
    """一个商品(合集)= 一个同步任务。title 仅用于任务卡片展示。"""
    title: str = Field("", max_length=255)
    image_ids: list[int] = Field(default_factory=list, max_length=200)


class SyncGroupsIn(BaseModel):
    groups: list[SyncGroup] = Field(default_factory=list, max_length=200)


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
    """同步(同步阻塞版,保留兼容):对选中暂存项服务端取图入库,并标侵权风险。"""
    if not body.image_ids:
        raise HTTPException(status_code=400, detail="image_ids 不能为空")
    return svc.sync_images(db, owner_id=user.id, image_ids=body.image_ids)


@router.post("/sync-async")
def sync_async(body: SyncGroupsIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """同步入库:一个商品(合集)= 一个任务,丢「最近任务」(像作图那样)。采集免费,不扣点。
    有 worker → 后台异步跑(并发=worker 并发,默认 3);没 worker → 就地同步执行(保证能跑通)。"""
    online = _worker_online()
    job_ids = []
    for g in body.groups:
        ids = svc.filter_syncable(db, owner_id=user.id, image_ids=g.image_ids)  # owner 隔离 + 去已同步
        if not ids:
            continue
        job = create_job(
            db, "collect_sync", owner_id=user.id, tool_id="collect_sync",
            params={"image_ids": ids, "title": (g.title or "采集同步")[:120], "count": len(ids)},
        )
        if online:
            enqueue_or_refund(run_tool, job, db, user, op="", n=0)  # 后台跑(免费 → n=0 不退点)
        else:
            run_tool(job.id)  # 无 worker → 就地同步执行,任务直接 done(不卡 pending)
        job_ids.append(job.id)
    if not job_ids:
        raise HTTPException(status_code=400, detail="没有可同步的项(可能已同步或为空)")
    return {"job_ids": job_ids, "count": len(job_ids), "background": online}
