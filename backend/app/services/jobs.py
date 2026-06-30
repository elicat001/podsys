"""异步作业(Job)队列与状态管理。

执行有两条实现并存,都把状态写回同一张 `Job` 表(唯一真相源),时间戳口径一致:
- **Celery**(`app/tasks.py`):独立 worker,**所有耗时 AI/本地作业端点的主路径**(抗重启、可隔离)。
- **BackgroundTasks**(`run_job` + `submit`):同进程后台,仅 `/api/process-async`(本地快管线)还在用。
`create_job` / `run_job` / `get_job` 为两者共用。
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import SessionLocal
from ..models_db import Job
from ..storage import mirror_job, new_job_id


def _now() -> datetime:
    return datetime.now(UTC)


# 作业卡死阈值:超过这么久还 pending/running 视为僵尸(worker 崩了/进程被打断)。
# 必须 >> 最慢的 AI 作业。图生视频:母帧退避重试(≤video_mufra_budget,熬中转站拥塞)+ 3 段并行轮询(≤25min)
# → 阈值给 50min(留足余量),免得误杀正在耐心重试母帧的慢视频(否则整单被回收=更糟)。
STUCK_MINUTES = 50

# 退点 op/笔数的【单一真相源】= app/tool_specs.py:TOOL_BILLING(run_tool 正常失败退点也读它)。
# 历史上这里另维护过一份 _KIND_REFUND_OP,漏登记 viduvideo/matting/imgreplace 致僵尸作业静默退错点 → 已收口到单表。


def reap_stuck_jobs(db: Session, minutes: int = STUCK_MINUTES) -> int:
    """把超时还 pending/running 的僵尸作业标 error + 退点(从未交付结果=应退)。返回清理条数。

    在 `/api/jobs` 列表端点惰性调用——列表自愈,不依赖重启。pending/running 行很少,全量取回
    Python 侧算龄(避开 DB naive datetime 与 aware 比较的坑)。
    ⚠ running 的从 started_at 算【实际执行时长】——否则 broker 排队久(多用户高峰)会让"刚开跑"的作业
    被按 created_at 误判卡死(母帧退避重试 + 视频生成本就接近阈值,排队时间不该再算进来)。
    pending 的(还没开跑)从 created_at 算:久未启动 = broker/worker 真出问题,该判卡死。
    """
    rows = db.execute(
        select(Job).where(Job.status.in_(("pending", "running")))
    ).scalars().all()
    now = _now()
    reaped = 0
    for job in rows:
        ref = job.started_at if (job.status == "running" and job.started_at is not None) else job.created_at
        if ref is None:
            continue
        if ref.tzinfo is None:
            ref = ref.replace(tzinfo=UTC)
        if (now - ref).total_seconds() < minutes * 60:
            continue
        job.status = "error"
        job.error = "作业超时或中断,已自动结束(可重试)"
        job.finished_at = now
        if job.owner_id is not None:
            from ..models_db import User
            from ..tool_specs import billing_n_for, billing_op_for
            from .billing import refund
            params = job.params or {}
            # 单一真相源:op(含 title「快速免费/AI 才扣」、collect_sync 免费等规则)+ 笔数(variants/视频按 n)都由 tool_specs 解析。
            op = billing_op_for(job.kind, params)
            n = billing_n_for(job.kind, params)
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
            mirror_job(job_id)  # 镜像产物进对象存储(local no-op);覆盖 /api/process-async + workflow_custom 等
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
