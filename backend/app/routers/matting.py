"""一键抠图路由。前缀 /api/matting。

纯去背景 → 透明 PNG(优先 rembg/u2net 智能抠图,边缘干净;缺包/失败兜底 pillow),
**不套图、不导出三件套**。耗时丢 Celery 后台(前端提交即走、任务中心看结果)。
计费:charge_for("process")(2 点);读图失败 → 400 + 退点。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy.orm import Session

from ..db import get_db
from ..models_db import User
from ..services.billing import charge_for
from ..tasks import run_tool
from ..web_utils import read_image_or_refund, submit_celery

router = APIRouter(prefix="/api/matting", tags=["matting"])


@router.post("")
async def matting(
    file: UploadFile = File(...),
    user: User = Depends(charge_for("process")),
    db: Session = Depends(get_db),
):
    """一键抠图 → Celery 后台作业,返回 {job_id, status:"pending"}(结果含透明 PNG image_url)。"""
    raw = await file.read()
    read_image_or_refund(raw, db, user, "process")  # 读图失败 → 400 + 退点
    return submit_celery(run_tool, db, user, kind="matting", tool_id="matting",
                         op="process", raw=raw, params={})
