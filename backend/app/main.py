"""FastAPI app — MVP main line: upload → extract → mockup → export."""
from __future__ import annotations
import io
from pathlib import Path
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image

from .config import settings
from . import storage
from .services.extract import extract_print
from .services.mockup import render_mockup, list_templates
from .services.export import export_production
from .services.generate import text_to_image, image_to_image
from .db import init_db
from .routers import auth as auth_router
from .routers import assets as assets_router
from .routers import design as design_router
from .routers import products as products_router

app = FastAPI(title="PODStudio API", version="0.2.0")
settings.ensure_dirs()
init_db()

app.include_router(auth_router.router)
app.include_router(assets_router.router)
app.include_router(design_router.router)
app.include_router(products_router.router)

FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent / "frontend"


@app.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "matting_provider": settings.matting_provider,
        "upscale_provider": settings.upscale_provider,
    }


@app.get("/api/templates")
def templates() -> list[dict]:
    return list_templates()


@app.post("/api/process")
async def process(
    file: UploadFile = File(...),
    template: str = Form("tshirt"),
    upscale: float = Form(1.0),
    width_cm: float = Form(30.0),
    height_cm: float = Form(40.0),
    dpi: int = Form(300),
):
    raw = await file.read()
    try:
        src = Image.open(io.BytesIO(raw))
        src.load()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"无法读取图片: {exc}") from exc

    job_id = storage.new_job_id()
    storage.upload_path(job_id).write_bytes(raw)

    # ① 提取印花(抠图 + 自动裁剪 + 可选放大)
    print_img = extract_print(src, upscale=upscale)
    print_path = storage.output_path(job_id, "print.png")
    print_img.save(print_path, format="PNG")

    # ② 套图预览
    mockup_img = render_mockup(print_img, template_id=template)
    mockup_path = storage.output_path(job_id, "mockup.png")
    mockup_img.save(mockup_path, format="PNG")

    # ③ 导出生产文件
    prod_path = storage.output_path(job_id, "production.png")
    meta = export_production(print_img, prod_path, width_cm=width_cm, height_cm=height_cm, dpi=dpi)

    return JSONResponse({
        "job_id": job_id,
        "print_url": storage.output_url(job_id, "print.png"),
        "mockup_url": storage.output_url(job_id, "mockup.png"),
        "production_url": storage.output_url(job_id, "production.png"),
        "production_meta": meta,
    })


@app.post("/api/generate")
async def generate(prompt: str = Form(...), size: str = Form("1024x1024")):
    """文生图(gpt-image / image2)。"""
    try:
        img = text_to_image(prompt, size=size)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"生成失败: {exc}") from exc
    job_id = storage.new_job_id()
    out = storage.output_path(job_id, "generated.png")
    img.save(out, format="PNG")
    return JSONResponse({"job_id": job_id, "image_url": storage.output_url(job_id, "generated.png")})


@app.post("/api/edit")
async def edit(
    file: UploadFile = File(...),
    prompt: str = Form(...),
    mask: UploadFile | None = File(None),
    size: str = Form("auto"),
):
    """图生图 / 改图 / 换装 / 换背景(gpt-image / image2 edit)。"""
    try:
        src = Image.open(io.BytesIO(await file.read())); src.load()
        mask_img = None
        if mask is not None:
            mask_img = Image.open(io.BytesIO(await mask.read())); mask_img.load()
        out_img = image_to_image(src, prompt, mask=mask_img, size=size)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"改图失败: {exc}") from exc
    job_id = storage.new_job_id()
    out = storage.output_path(job_id, "edited.png")
    out_img.save(out, format="PNG")
    return JSONResponse({"job_id": job_id, "image_url": storage.output_url(job_id, "edited.png")})


@app.get("/files/{job_id}/{name}")
def get_file(job_id: str, name: str):
    p = settings.outputs_dir / job_id / name
    if not p.exists():
        raise HTTPException(status_code=404, detail="file not found")
    return FileResponse(p)


# serve the static frontend at root (mounted last so /api/* wins)
if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
