"""转矢量图路由:位图 → SVG(本地 vtracer/cv2 真矢量描摹,op=process 扣 2)。

高保真描摹大图可能 10~30s → **Celery 后台作业**:读图/参数校验同步先做(失败即 400 + 退点),
再落盘 + 入队,立即返回 `{job_id, status:"pending"}`,前端轮询 `GET /api/jobs/{id}`。
worker 内描摹失败由 `run_job_in_worker` 自动退点;broker 不可用由 `submit_celery` 退点 + 502。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from ..db import get_db
from ..models_db import User
from ..services.billing import charge_for, refund
from ..tasks import run_tool
from ..web_utils import read_image_or_refund as _read_image
from ..web_utils import submit_celery

router = APIRouter(prefix="/api/vectorize", tags=["vectorize"])


@router.post("")
def vectorize(
    file: UploadFile = File(...),
    colors: int = Form(8),
    preset: str = Form("auto"),
    user: User = Depends(charge_for("process")),
    db: Session = Depends(get_db),
):
    """位图转 SVG(后台作业)。读图失败或 colors 越界 → 同步 400 + 退点;否则返回 job_id 供轮询。"""
    raw = file.file.read()
    _read_image(raw, db, user, "process")                # 读图失败 → 400 + 退点(仅校验可解码)
    if not (2 <= colors <= 64):                           # 参数非法 → 早失败 400 + 退点(不进后台)
        refund(db, user, "process")
        raise HTTPException(status_code=400, detail=f"colors 必须在 2..64 之间,收到 {colors}")

    return submit_celery(run_tool, db, user, kind="vectorize", tool_id="vectorize", op="process",
                         raw=raw, params={"colors": colors, "preset": preset})
