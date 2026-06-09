"""异步作业(Job)队列与状态管理。

执行有两条实现并存,都把状态写回同一张 `Job` 表(唯一真相源),时间戳口径一致:
- **Celery**(`app/tasks.py`):独立 worker,**所有耗时 AI/本地作业端点的主路径**(抗重启、可隔离)。
- **BackgroundTasks**(`run_job` + `submit`):同进程后台,仅 `/api/process-async`(本地快管线)还在用。
`create_job` / `run_job` / `get_job` 为两者共用。
"""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Callable
from sqlalchemy.orm import Session
from ..db import SessionLocal
from ..models_db import Job
from ..storage import new_job_id


def _now() -> datetime:
    return datetime.now(timezone.utc)


def create_job(db: Session, kind: str, params: dict | None = None,
               owner_id: int | None = None, tool_id: str = "") -> Job:
    """创建一条 pending 作业并落库。tool_id=前端工具 id(用于「我的空间」分组展示)。"""
    job = Job(
        id=new_job_id(),
        kind=kind,
        status="pending",
        tool_id=tool_id,
        params=params or {},
        result={},
        error="",
        owner_id=owner_id,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def run_job(job_id: str, fn: Callable[[], dict]) -> None:
    """执行作业:置 running → 调 fn() → done/error。可被 BackgroundTasks 调用。

    开新 Session(BackgroundTasks 运行在请求生命周期之外,不能复用请求的 session)。
    """
    db = SessionLocal()
    try:
        job = db.get(Job, job_id)
        if job is None:
            return
        job.status = "running"
        job.started_at = _now()
        db.commit()
        try:
            result = fn()
            job.status = "done"
            job.result = result if isinstance(result, dict) else {"value": result}
            job.error = ""
        except Exception as exc:  # noqa: BLE001
            job.status = "error"
            # P1-1:保留异常类型,无 message 的异常(如 KeyError())也能定位
            job.error = f"{type(exc).__name__}: {exc}".strip()
        job.finished_at = _now()
        db.commit()
    finally:
        db.close()


def get_job(db: Session, job_id: str) -> Job | None:
    """按 id 取作业,不存在返回 None。"""
    return db.get(Job, job_id)


def submit(background_tasks, db: Session, kind: str, fn: Callable[[], dict],
           params: dict | None = None, owner_id: int | None = None) -> str:
    """便捷封装:建 pending 作业 + 注册后台执行,返回 job_id(供仍用 BackgroundTasks 的端点)。"""
    job = create_job(db, kind, params=params, owner_id=owner_id)
    background_tasks.add_task(run_job, job.id, fn)
    return job.id
