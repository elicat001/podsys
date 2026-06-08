"""转矢量图路由:位图 → SVG(本地 vtracer/cv2 真矢量描摹,op=process 扣 2)。

高保真描摹大图可能 10~30s → 改**后台作业**(`submit_ai_job`):读图/参数校验同步先做(失败即
400 + 退点),然后立即返回 `{job_id, status:"pending"}`,前端轮询 `GET /api/jobs/{id}`。
避免长连接在慢网/代理下 Failed to fetch;与 print-extract/generate 等耗时端点行为统一。
作业内描摹失败由 `submit_ai_job` 自动退点。
"""
from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from .. import storage
from ..db import get_db
from ..models_db import User
from ..services.billing import charge_for, refund
from ..services.jobs import submit_ai_job
from ..services.vectorize import to_svg
from ..web_utils import read_image_or_refund as _read_image

router = APIRouter(prefix="/api/vectorize", tags=["vectorize"])


@router.post("")
def vectorize(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    colors: int = Form(8),
    preset: str = Form("auto"),
    user: User = Depends(charge_for("process")),
    db: Session = Depends(get_db),
):
    """位图转 SVG(后台作业)。读图失败或 colors 越界 → 同步 400 + 退点;否则返回 job_id 供轮询。"""
    raw = file.file.read()
    src = _read_image(raw, db, user, "process")          # 读图失败 → 400 + 退点
    if not (2 <= colors <= 64):                           # 参数非法 → 早失败 400 + 退点(不进后台)
        refund(db, user, "process")
        raise HTTPException(status_code=400, detail=f"colors 必须在 2..64 之间,收到 {colors}")

    def _work(jid: str) -> dict:
        svg, rect_count = to_svg(src, colors=colors, preset=preset)
        storage.output_path(jid, "vector.svg").write_text(svg, encoding="utf-8")
        return {
            "svg_url": storage.output_url(jid, "vector.svg"),
            "rect_count": rect_count,
            "colors": colors,
        }

    jid = submit_ai_job(background_tasks, db, "vectorize", user.id, _work, refund_op="process")
    return {"job_id": jid, "status": "pending"}
