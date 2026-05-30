"""设计工具:多联画/多图裁剪、批量套图。"""
from __future__ import annotations
import io
from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from PIL import Image
from .. import storage
from ..services.split import split_panels
from ..services.mockup import render_batch

router = APIRouter(prefix="/api/design", tags=["design"])


def _read(raw: bytes) -> Image.Image:
    try:
        im = Image.open(io.BytesIO(raw)); im.load(); return im
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"无法读取图片: {exc}") from exc


@router.post("/split")
async def split(file: UploadFile = File(...), mode: str = Form("horizontal"),
                panels: int = Form(3), rows: int = Form(2), cols: int = Form(2)):
    img = _read(await file.read())
    try:
        parts = split_panels(img, mode=mode, panels=panels, rows=rows, cols=cols)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    job_id = storage.new_job_id()
    urls = []
    for i, p in enumerate(parts):
        name = f"panel_{i+1}.png"
        p.save(storage.output_path(job_id, name), format="PNG")
        urls.append(storage.output_url(job_id, name))
    return {"job_id": job_id, "count": len(urls), "panels": urls}


@router.post("/mockup-batch")
async def mockup_batch(file: UploadFile = File(...), templates: str = Form("tshirt,tote,canvas")):
    img = _read(await file.read())
    tids = [t.strip() for t in templates.split(",") if t.strip()]
    results = render_batch(img, tids)
    job_id = storage.new_job_id()
    out = {}
    for tid, m in results.items():
        name = f"mockup_{tid}.png"
        m.save(storage.output_path(job_id, name), format="PNG")
        out[tid] = storage.output_url(job_id, name)
    return {"job_id": job_id, "mockups": out}
