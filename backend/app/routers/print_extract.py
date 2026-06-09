"""印花提取接口。

引擎在 `services/print_extract.py` 决定:默认 AI 重绘(慢 ~90s),无 key/失败降级本地保真(快)。
本路由按速度分两条响应:
- **AI 路径(慢)→ Celery 作业**:落盘原图 + 建 Job + 入队,立即返回 `{job_id, status:"pending"}`,
  前端轮询 `GET /api/jobs/{id}`。闭包不能跨进程,故只传 job_id 给 worker,worker 自己按 id
  读盘干活(见 `tasks.run_print_extract`)。避免同步占满 90s 连接,并与其它 AI 工具行为统一。
- **本地路径(快)→ 同步**直接返回结果(无 key 时即此路径,离线测试走这条、保持确定性)。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from .. import storage
from ..config import settings
from ..db import get_db
from ..models_db import User
from ..services.billing import charge_for, refund
from ..services.jobs import create_job
from ..services.library import save_as_asset
from ..services.print_extract import extract_print_design, save_print_outputs
from ..tasks import run_print_extract
from ..web_utils import enqueue_or_refund, read_image_or_refund

router = APIRouter(prefix="/api/print-extract", tags=["print-extract"])


@router.post("")
def print_extract(
    file: UploadFile = File(...),
    user: User = Depends(charge_for("process")),
    db: Session = Depends(get_db),
):
    raw = file.file.read()
    src = read_image_or_refund(raw, db, user, "process")  # 读图失败 → 400 + 退点

    # AI 路径(慢)→ Celery 作业:落盘原图 + 建 Job + 入队;失败退点由 worker 内
    # run_job_in_worker(refund_op="process") 兜底。
    if settings.print_extract_ai and settings.openai_api_key:
        job = create_job(db, "print-extract", owner_id=user.id, tool_id="extract")
        storage.upload_path(job.id).write_bytes(raw)  # worker 按 job_id 读这张原图
        enqueue_or_refund(run_print_extract, job, db, user, "process")  # broker 挂了→退点+502
        return {"job_id": job.id, "status": "pending"}

    # 本地路径(快)→ 同步直接返回(无 key 时即此;离线测试走这条)
    job_id = storage.new_job_id()
    storage.upload_path(job_id).write_bytes(raw)
    try:
        design, meta = extract_print_design(src)
    except ValueError as exc:
        refund(db, user, "process")
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    url, result = save_print_outputs(job_id, design, meta)
    save_as_asset(db, user.id, design, "印花提取", url, source="generated")
    return {"job_id": job_id, **result}
