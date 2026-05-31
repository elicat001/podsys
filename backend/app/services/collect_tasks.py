"""采集任务服务层 — 创建任务/采集图、序列化辅助。

纯 DB 逻辑,不依赖 FastAPI;路由层负责鉴权与 HTTP 语义。
对每个 URL 用 collectors 的纯函数判定平台并升级到高清地址。
"""
from __future__ import annotations
from sqlalchemy.orm import Session
from .. import storage
from ..models_collect import CollectionTask, CollectedImage
from .collectors import detect_platform, upgrade_to_hires


def create_task(
    db: Session,
    owner_id: int,
    urls: list[str],
    source: str = "plugin",
) -> CollectionTask:
    """建一个采集任务,并为每个 url 建一条采集图(含平台/高清地址)。"""
    task = CollectionTask(
        id=storage.new_job_id(),
        owner_id=owner_id,
        source=source,
        status="collected",
        count=len(urls),
    )
    db.add(task)
    for url in urls:
        platform = detect_platform(url)
        hires = upgrade_to_hires(url, platform)
        db.add(
            CollectedImage(
                task_id=task.id,
                url=url,
                hires_url=hires,
                platform=platform,
            )
        )
    db.commit()
    db.refresh(task)
    return task


def task_to_dict(task: CollectionTask) -> dict:
    return {
        "id": task.id,
        "source": task.source,
        "status": task.status,
        "count": task.count,
        "created_at": task.created_at.isoformat() if task.created_at else None,
    }


def image_to_dict(img: CollectedImage) -> dict:
    return {
        "id": img.id,
        "task_id": img.task_id,
        "url": img.url,
        "hires_url": img.hires_url,
        "platform": img.platform,
        "title": img.title,
        "selected": img.selected,
    }
