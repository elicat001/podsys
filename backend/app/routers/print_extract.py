"""印花提取接口:把图里印刷的图案单独抠出来,输出透明 PNG。

与 /api/process(一键抠图,rembg 去背景留主体)区分开:这里走『识图定位 + 本地去布料』。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from PIL import Image
from sqlalchemy.orm import Session

from .. import storage
from ..db import get_db
from ..models_db import User
from ..services.billing import charge_for, refund
from ..services.library import save_as_asset
from ..services.print_extract import extract_print_design
from ..web_utils import read_image_or_refund

router = APIRouter(prefix="/api/print-extract", tags=["print-extract"])


@router.post("")
def print_extract(
    file: UploadFile = File(...),
    user: User = Depends(charge_for("process")),
    db: Session = Depends(get_db),
):
    raw = file.file.read()
    src = read_image_or_refund(raw, db, user, "process")
    job_id = storage.new_job_id()
    storage.upload_path(job_id).write_bytes(raw)  # 保存原图,便于排查真实失败案例
    try:
        design, meta = extract_print_design(src)
    except ValueError as exc:
        refund(db, user, "process")
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    out = storage.output_path(job_id, "design.png")
    design.save(out, format="PNG")
    url = storage.output_url(job_id, "design.png")
    save_as_asset(db, user.id, design, "印花提取", url, source="generated")

    # 白底版:透明区填白,便于下载/预览(深色看图器里透明会显黑)。透明版 design.png 仍保留(套版/印刷用)。
    white = Image.new("RGB", design.size, (255, 255, 255))
    white.paste(design, (0, 0), design)
    white.save(storage.output_path(job_id, "design_white.png"), format="PNG")
    white_url = storage.output_url(job_id, "design_white.png")
    return {"job_id": job_id, "image_url": url, "white_url": white_url, **meta}
