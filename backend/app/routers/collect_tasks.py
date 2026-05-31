"""采集任务路由 — 任务/采集图列表的持久化与选取。"""
from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from ..db import get_db
from ..models_db import User
from ..models_collect import CollectionTask, CollectedImage
from ..auth import current_user
from ..services import collect_tasks as svc

router = APIRouter(prefix="/api/collect-tasks", tags=["collect-tasks"])


class CreateTaskIn(BaseModel):
    source: str = Field("plugin", max_length=64)
    # P1-1:限制条数与单 url 长度,防单请求撑爆内存/DB
    urls: list[str] = Field(default_factory=list, max_length=500)


class SelectIn(BaseModel):
    image_ids: list[int] = Field(default_factory=list)


def _get_owned_task(task_id: str, user: User, db: Session) -> CollectionTask:
    task = db.get(CollectionTask, task_id)
    if not task or task.owner_id != user.id:
        raise HTTPException(status_code=404, detail="采集任务不存在")
    return task


@router.post("")
def create_task(body: CreateTaskIn, user: User = Depends(current_user),
                db: Session = Depends(get_db)):
    if not body.urls:
        raise HTTPException(status_code=400, detail="urls 不能为空")
    if any(len(u) > 2048 for u in body.urls):
        raise HTTPException(status_code=400, detail="单个 url 过长(上限 2048)")
    task = svc.create_task(db, owner_id=user.id, urls=body.urls, source=body.source)
    return {"task_id": task.id, "count": task.count}


@router.get("")
def list_tasks(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.execute(
        select(CollectionTask)
        .where(CollectionTask.owner_id == user.id)
        .order_by(CollectionTask.created_at.desc())
    ).scalars().all()
    return [svc.task_to_dict(t) for t in rows]


@router.get("/{task_id}")
def get_task(task_id: str, user: User = Depends(current_user),
             db: Session = Depends(get_db)):
    task = _get_owned_task(task_id, user, db)
    images = db.execute(
        select(CollectedImage)
        .where(CollectedImage.task_id == task.id)
        .order_by(CollectedImage.id.asc())
    ).scalars().all()
    data = svc.task_to_dict(task)
    data["images"] = [svc.image_to_dict(i) for i in images]
    return data


@router.post("/{task_id}/select")
def select_images(task_id: str, body: SelectIn, user: User = Depends(current_user),
                  db: Session = Depends(get_db)):
    task = _get_owned_task(task_id, user, db)
    updated = 0
    if body.image_ids:
        rows = db.execute(
            select(CollectedImage).where(
                CollectedImage.task_id == task.id,
                CollectedImage.id.in_(body.image_ids),
            )
        ).scalars().all()
        for img in rows:
            img.selected = True
            updated += 1
        db.commit()
    return {"updated": updated}
