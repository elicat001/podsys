"""图案处理工具路由:扩图 / 去水印(gpt-image edit)+ 裁剪压缩(离线 Pillow)。

计费/错误范式同 main.py:charge_for 预扣 → 读图失败或 AI 失败 → refund + HTTPException。
"""
from __future__ import annotations

import io

from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Depends
from fastapi.responses import JSONResponse
from PIL import Image
from sqlalchemy.orm import Session

from .. import storage
from ..ai.openai_image import OpenAIImageClient
from ..db import get_db
from ..models_db import User
from ..services.billing import charge_for, refund
from ..services.image_tools import compress_image
from ..web_utils import read_image_or_refund as _read_image

router = APIRouter(prefix="/api/image-tools", tags=["image-tools"])

# 扩图(outpaint)用的 prompt 模板
_EXPAND_PROMPT = (
    "Outpaint and extend this image naturally beyond its current borders, "
    "seamlessly continuing the existing content, lighting and style to fill "
    "a larger canvas. Keep the original subject intact and centered."
)
_DEWATERMARK_PROMPT = (
    "Remove any watermarks, logos, text overlays and signatures from this image. "
    "Cleanly reconstruct the underlying content so the result looks natural and "
    "watermark-free, keeping the main subject and style intact."
)


def _edit_endpoint(raw: bytes, prompt: str, size: str, db: Session, user: User) -> str:
    """共用:读图 → gpt-image edit → 落盘,返回 image_url。失败退点 + 502。"""
    src = _read_image(raw, db, user, "edit")
    try:
        client = OpenAIImageClient()
        out_img = client.edit(src, prompt, size=size)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        refund(db, user, "edit")
        raise HTTPException(status_code=502, detail="处理失败,请稍后重试") from exc
    job_id = storage.new_job_id()
    out_img.save(storage.output_path(job_id, "result.png"), format="PNG")
    return job_id


@router.post("/expand")
async def expand(
    file: UploadFile = File(...),
    prompt: str = Form(""),
    size: str = Form("auto"),
    user: User = Depends(charge_for("edit")),
    db: Session = Depends(get_db),
):
    """扩图(outpaint via gpt-image edit)。无 key → 502 + 退点。"""
    raw = await file.read()
    full_prompt = f"{_EXPAND_PROMPT} {prompt}".strip()
    job_id = _edit_endpoint(raw, full_prompt, size, db, user)
    return JSONResponse({"job_id": job_id, "image_url": storage.output_url(job_id, "result.png")})


@router.post("/dewatermark")
async def dewatermark(
    file: UploadFile = File(...),
    prompt: str = Form(""),
    size: str = Form("auto"),
    user: User = Depends(charge_for("edit")),
    db: Session = Depends(get_db),
):
    """去水印(gpt-image edit)。无 key → 502 + 退点。"""
    raw = await file.read()
    full_prompt = f"{_DEWATERMARK_PROMPT} {prompt}".strip()
    job_id = _edit_endpoint(raw, full_prompt, size, db, user)
    return JSONResponse({"job_id": job_id, "image_url": storage.output_url(job_id, "result.png")})


@router.post("/compress")
async def compress(
    file: UploadFile = File(...),
    target_w: int = Form(0),
    target_h: int = Form(0),
    quality: int = Form(85),
    fmt: str = Form("jpeg"),
    user: User = Depends(charge_for("process")),
    db: Session = Depends(get_db),
):
    """裁剪压缩(纯离线 Pillow,op=process 扣 2)。

    返回压缩后文件 + 原始/压缩后字节数 + 尺寸/格式。
    """
    raw = await file.read()
    original_bytes = len(raw)
    src = _read_image(raw, db, user, "process")
    try:
        _out_img, encoded, info = compress_image(
            src, target_w=target_w, target_h=target_h, quality=quality, fmt=fmt
        )
    except ValueError as exc:
        refund(db, user, "process")
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        refund(db, user, "process")
        raise HTTPException(status_code=400, detail="压缩失败") from exc

    job_id = storage.new_job_id()
    ext = "jpg" if info["pil_format"] == "JPEG" else info["format"]
    name = f"compressed.{ext}"
    # 直接写编码后的字节,保证落盘文件与 output_bytes 完全一致
    storage.output_path(job_id, name).write_bytes(encoded)

    return JSONResponse({
        "job_id": job_id,
        "image_url": storage.output_url(job_id, name),
        "original_bytes": original_bytes,
        "output_bytes": info["output_bytes"],
        "width": info["width"],
        "height": info["height"],
        "format": info["format"],
    })
