"""作业状态查询 API(需鉴权,按 owner 隔离 —— P0-3)。"""
from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import select
from ..db import get_db
from ..models_db import Job, User
from ..auth import current_user
from ..services.jobs import get_job

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


def _serialize(job: Job) -> dict:
    return {
        "id": job.id,
        "kind": job.kind,
        "status": job.status,
        "result": job.result,
        "error": job.error,
        "created_at": job.created_at.isoformat() if job.created_at else None,
    }


@router.get("")
def list_jobs(kind: str | None = None, db: Session = Depends(get_db),
              user: User = Depends(current_user)):
    """列出当前用户的作业(最近在前),可按 kind 过滤。"""
    stmt = select(Job).where(Job.owner_id == user.id)
    if kind:
        stmt = stmt.where(Job.kind == kind)
    stmt = stmt.order_by(Job.created_at.desc())
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
