"""路由层公共工具:统一「读图失败即退点 + 400」范式(消除 4 个 router 的重复,评审 P1-3)。"""
from __future__ import annotations

import io

from fastapi import HTTPException
from PIL import Image
from sqlalchemy.orm import Session

from .models_db import Job, User
from .services.billing import refund


def read_image_or_refund(raw: bytes, db: Session, user: User, op: str) -> Image.Image:
    """解码上传图片;失败则退回已预扣的 `op` 点数,再抛 400。

    适用于「单次预扣」端点(charge_for 扣 1 笔)。按张多扣的端点(如 variants)
    需自行处理退点笔数,勿用本函数。
    """
    try:
        im = Image.open(io.BytesIO(raw))
        im.load()
        return im
    except Exception as exc:  # noqa: BLE001
        refund(db, user, op)
        raise HTTPException(status_code=400, detail=f"无法读取图片: {exc}") from exc


def enqueue_or_refund(task, job: Job, db: Session, user: User, op: str, n: int = 1) -> None:
    """把作业投递到 Celery(`task.delay(job_id)`);若 broker 不可用,退点 + 标记作业失败 + 502。

    为什么需要:扣点在 `charge_for` 发生(早于入队)。若此刻 Redis/worker 不可用,
    `.delay()` 会抛连接错误——必须退回已预扣的 n 笔,否则用户白扣点(P0-2「失败必退点」)。
    """
    try:
        task.delay(job.id)
    except Exception as exc:  # noqa: BLE001 — broker 任何不可用都按可恢复故障处理
        for _ in range(n):
            refund(db, user, op)
        job.status = "error"
        job.error = f"队列不可用: {type(exc).__name__}"
        db.commit()
        raise HTTPException(status_code=502, detail="后台队列暂时不可用,请稍后重试(点数已退回)") from exc


def submit_celery(task, db: Session, user: User, *, kind: str, tool_id: str, op: str,
                  raw: bytes | None = None, params: dict | None = None, n: int = 1,
                  mask_raw: bytes | None = None) -> dict:
    """异步端点的统一收尾:建 Job(存 params)+ 落输入图(若有)+ 入队(broker 挂了退点)。

    返回 {job_id, status:"pending"}。闭包不能跨进程,故输入通过 disk(upload_path)+ params 传给 worker。
    超出存储配额(默认 2GB)→ 退回已扣的 n 笔 + 413(不让继续往满的盘里塞)。
    """
    from . import storage
    from .services.jobs import create_job
    from .services.quota import usage
    if usage(db, user.id)["over"]:
        for _ in range(n):
            refund(db, user, op)
        raise HTTPException(status_code=413,
                            detail="存储空间已用满(上限 2GB),请到「我的空间」清理回收站后重试(点数已退回)")
    job = create_job(db, kind, owner_id=user.id, tool_id=tool_id, params=params or {})
    if raw is not None:
        storage.upload_path(job.id).write_bytes(raw)
    if mask_raw is not None:
        storage.upload_path(f"{job.id}_mask").write_bytes(mask_raw)
    enqueue_or_refund(task, job, db, user, op, n)
    return {"job_id": job.id, "status": "pending"}
