"""转矢量图路由:位图 → SVG(纯离线 Pillow,op=process 扣 2)。

计费/错误范式同 image_tools.py:charge_for 预扣 → 读图失败或参数非法 → refund + 400。
"""
from __future__ import annotations

from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from .. import storage
from ..db import get_db
from ..models_db import User
from ..services.billing import charge_for, refund
from ..services.vectorize import to_svg
from ..web_utils import read_image_or_refund as _read_image

router = APIRouter(prefix="/api/vectorize", tags=["vectorize"])


@router.post("")
async def vectorize(
    file: UploadFile = File(...),
    colors: int = Form(8),
    user: User = Depends(charge_for("process")),
    db: Session = Depends(get_db),
):
    """位图转 SVG。读图失败或 colors 越界 → 退点 + 400。"""
    raw = await file.read()
    src = _read_image(raw, db, user, "process")
    try:
        svg, rect_count = to_svg(src, colors=colors)
    except ValueError as exc:
        refund(db, user, "process")
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    job_id = storage.new_job_id()
    storage.output_path(job_id, "vector.svg").write_text(svg, encoding="utf-8")
    return JSONResponse({
        "job_id": job_id,
        "svg_url": storage.output_url(job_id, "vector.svg"),
        "rect_count": rect_count,
        "colors": colors,
    })
