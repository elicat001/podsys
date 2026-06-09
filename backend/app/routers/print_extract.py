"""印花提取接口。

引擎在 `services/print_extract.py` 决定:默认 AI 重绘(慢 ~90s),无 key/失败降级本地保真(快)。
本路由按速度分两条响应:
- **AI 路径(慢)→ Celery 作业**:落盘原图 + 建 Job + 入队,立即返回 `{job_id, status:"pending"}`,
  前端轮询 `GET /api/jobs/{id}`。闭包不能跨进程,故只传 job_id 给 worker,worker 自己按 id
  读盘干活(见 `tasks.run_print_extract`)。避免同步占满 90s 连接,并与其它 AI 工具行为统一。
- **本地路径(快)→ 同步**直接返回结果(无 key 时即此路径,离线测试走这条、保持确定性)。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..models_db import User
from ..services.billing import charge_for, refund
from ..services.jobs import create_job
from ..tasks import run_print_extract
from ..web_utils import enqueue_or_refund, read_image_or_refund

router = APIRouter(prefix="/api/print-extract", tags=["print-extract"])


@router.post("")
def print_extract(
    file: UploadFile = File(...),
    engine: str = Form("auto"),   # ai=智能(gpt 重绘,需 key)| fast=快速(本地保真)| auto=有 key 走 AI 否则本地
    user: User = Depends(charge_for("process")),
    db: Session = Depends(get_db),
):
    """印花提取 → 一律 Celery 后台作业(前端提交即走、任务中心看结果)。engine 决定 worker 用哪个引擎。"""
    from .. import storage
    raw = file.file.read()
    read_image_or_refund(raw, db, user, "process")  # 读图失败 → 400 + 退点

    if engine == "ai" and not settings.openai_api_key:
        refund(db, user, "process")
        raise HTTPException(status_code=502, detail="智能运行需配置 AI key;可改用「快速运行」(本地)")
    # 解析最终引擎:fast=本地;ai=AI;auto=有 key 走 AI 否则本地
    eng = "ai" if (engine == "ai" or (engine == "auto" and settings.print_extract_ai and settings.openai_api_key)) else "fast"

    job = create_job(db, "print-extract", owner_id=user.id, tool_id="extract", params={"engine": eng})
    storage.upload_path(job.id).write_bytes(raw)
    enqueue_or_refund(run_print_extract, job, db, user, "process")  # broker 挂了→退点+502
    return {"job_id": job.id, "status": "pending"}
