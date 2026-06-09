"""作业状态查询 API(需鉴权,按 owner 隔离 —— P0-3)。"""
from __future__ import annotations
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import select
from ..db import get_db
from ..models_db import Job, User
from ..auth import current_user
from ..services.jobs import get_job

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


def _iso_utc(dt: datetime | None) -> str | None:
    """序列化为带 UTC 偏移的 ISO 串。作业时间戳都按 UTC 存(_now=now(utc)),但 SQLite 取回是
    naive,直接 isoformat() 不带偏移 → 浏览器会当本地时区解析(差 8 小时)。这里强制补 +00:00。"""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _serialize(job: Job) -> dict:
    started = job.started_at
    finished = job.finished_at
    # 运行时长(秒):优先用 finished-started;还在跑则不给(前端按 now-started 实时算)。
    duration = (finished - started).total_seconds() if started and finished else None
    return {
        "id": job.id,
        "kind": job.kind,
        "tool_id": job.tool_id,           # 前端据此映射工具名/图标/大模块→小模块分组
        "status": job.status,
        "result": job.result,
        "error": job.error,
        "created_at": _iso_utc(job.created_at),
        "started_at": _iso_utc(started),
        "finished_at": _iso_utc(finished),
        "duration_sec": duration,
    }


@router.get("")
def list_jobs(kind: str | None = None, limit: int | None = None,
              db: Session = Depends(get_db), user: User = Depends(current_user)):
    """列出当前用户的作业(最近在前),可按 kind 过滤;limit 限制条数(顶栏「最近任务」用)。"""
    stmt = select(Job).where(Job.owner_id == user.id)
    if kind:
        stmt = stmt.where(Job.kind == kind)
    stmt = stmt.order_by(Job.created_at.desc())
    if limit and limit > 0:
        stmt = stmt.limit(limit)
    rows = db.execute(stmt).scalars().all()
    return [_serialize(j) for j in rows]


@router.get("/{job_id}")
def read_job(job_id: str, db: Session = Depends(get_db),
             user: User = Depends(current_user)):
    """查询单个作业状态;不存在或非本人作业均 404(避免探测他人作业是否存在)。"""
    job = get_job(db, job_id)
    if job is None or job.owner_id != user.id:
        raise HTTPException(status_code=404, detail="job not found")
    return _serialize(job)
