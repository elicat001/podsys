"""异步作业(Job)队列与状态管理。

执行有两条实现并存,都把状态写回同一张 `Job` 表(唯一真相源),时间戳口径一致:
- **Celery**(`app/tasks.py`):独立 worker,**所有耗时 AI/本地作业端点的主路径**(抗重启、可隔离)。
- **BackgroundTasks**(`run_job` + `submit`):同进程后台,仅 `/api/process-async`(本地快管线)还在用。
`create_job` / `run_job` / `get_job` 为两者共用。
"""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Callable
from sqlalchemy import select
from sqlalchemy.orm import Session
from ..db import SessionLocal
from ..models_db import Job
from ..storage import new_job_id


def _now() -> datetime:
    return datetime.now(timezone.utc)


# 作业卡死阈值:超过这么久还 pending/running 视为僵尸(worker 崩了/进程被打断)。
# 必须 >> 最慢的 AI 作业(实测印花提取约 4~5min),否则会误杀正常在跑的任务。
STUCK_MINUTES = 30

# kind → 退点 op(与各 router 的 charge_for/扣点 op 对齐)。未列出的默认 "edit"。
# 注意:按张多扣的 kind(variants/mockup-batch/mockup-replace)退点笔数 = params["n"](见 reap)。
_KIND_REFUND_OP = {
    "process": "process", "print-extract": "process", "upscale": "process", "vectorize": "process",
    "seamless": "process", "compress": "process", "ipguard": "process",
    "generate": "generate",
    "edit": "edit", "variants": "edit", "fuse": "edit", "restyle": "edit", "meme": "edit",
    "expand": "edit", "dewatermark": "edit", "tryon": "edit", "pet-costume": "edit", "group-photo": "edit",
    "mockup": "asset", "mockup-batch": "asset", "mockup-replace": "asset", "production": "asset",
    "title": "title",
}


def reap_stuck_jobs(db: Session, minutes: int = STUCK_MINUTES) -> int:
    """把超时还 pending/running 的僵尸作业标 error + 退点(从未交付结果=应退)。返回清理条数。

    在 `/api/jobs` 列表端点惰性调用——列表自愈,不依赖重启。pending/running 行很少,全量取回
    Python 侧按 created_at 算龄(避开 SQLite naive datetime 与 aware 比较的坑)。
    """
    rows = db.execute(
        select(Job).where(Job.status.in_(("pending", "running")))
    ).scalars().all()
    now = _now()
    reaped = 0
    for job in rows:
        ca = job.created_at
        if ca is None:
            continue
        if ca.tzinfo is None:
            ca = ca.replace(tzinfo=timezone.utc)
        if (now - ca).total_seconds() < minutes * 60:
            continue
        job.status = "error"
        job.error = "作业超时或中断,已自动结束(可重试)"
        job.finished_at = now
        if job.owner_id is not None:
            from ..models_db import User
            from .billing import refund
            params = job.params or {}
            op = _KIND_REFUND_OP.get(job.kind, "edit")
            # 按张多扣的(variants/mockup-batch/mockup-replace)退 n 笔,对齐扣点;其余 1 笔。
            n = int(params.get("n", 1) or 1)
            # 标题「快速」本就免费(只有「智能/AI」扣点),不退;避免给未扣点的任务白送积分。
            if job.kind == "title" and params.get("engine") != "ai":
                op = None
            if op:
                u = db.get(User, job.owner_id)
                if u is not None:
                    for _ in range(n):
                        refund(db, u, op)
        reaped += 1
    if reaped:
        db.commit()
    return reaped


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
