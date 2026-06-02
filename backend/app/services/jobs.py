"""异步作业(Job)队列与状态管理 — 不依赖 Celery/Redis,基于 BackgroundTasks。"""
from __future__ import annotations
from typing import Callable
from sqlalchemy.orm import Session
from ..db import SessionLocal
from ..models_db import Job
from ..storage import new_job_id


def create_job(db: Session, kind: str, params: dict | None = None,
               owner_id: int | None = None) -> Job:
    """创建一条 pending 作业并落库。"""
    job = Job(
        id=new_job_id(),
        kind=kind,
        status="pending",
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
        db.commit()
    finally:
        db.close()


def get_job(db: Session, job_id: str) -> Job | None:
    """按 id 取作业,不存在返回 None。"""
    return db.get(Job, job_id)


def submit(background_tasks, db: Session, kind: str, fn: Callable[[], dict],
           params: dict | None = None, owner_id: int | None = None) -> str:
    """便捷封装:建 pending 作业 + 注册后台执行,返回 job_id。供其他端点复用。"""
    job = create_job(db, kind, params=params, owner_id=owner_id)
    background_tasks.add_task(run_job, job.id, fn)
    return job.id


def refund_in_background(owner_id: int, op: str, n: int = 1) -> None:
    """后台作业失败退点:请求 session 已关,必须用独立 session;按 n 笔退。"""
    from ..models_db import User
    from .billing import refund
    s = SessionLocal()
    try:
        u = s.get(User, owner_id)
        if u is None:
            return
        for _ in range(n):
            refund(s, u, op)
    finally:
        s.close()


def save_asset_in_background(owner_id: int, image, name: str, url: str,
                             source: str = "generated") -> None:
    """后台作业内入库素材:用独立 session(请求 session 已关)。save_as_asset 自身吞错。"""
    from .library import save_as_asset
    s = SessionLocal()
    try:
        save_as_asset(s, owner_id, image, name, url, source=source)
    finally:
        s.close()


def submit_ai_job(background_tasks, db: Session, kind: str, owner_id: int,
                  work: Callable[[str], dict], *, refund_op: str, refund_n: int = 1,
                  params: dict | None = None) -> str:
    """建作业 + 后台执行 work(job_id);work 抛错时按 refund_n 笔退 refund_op,再交 run_job 记 error。

    work 接收 job_id(可作产物存储目录),返回结果 dict(存入 job.result)。
    用于 gpt-image 等耗时端点:HTTP 立即返回 job_id,前端轮询 GET /api/jobs/{id}。
    """
    job = create_job(db, kind, params=params, owner_id=owner_id)
    jid = job.id

    def _wrapped() -> dict:
        try:
            return work(jid)
        except Exception:
            refund_in_background(owner_id, refund_op, refund_n)
            raise

    background_tasks.add_task(run_job, jid, _wrapped)
    return jid
