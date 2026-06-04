"""图案处理工具路由:扩图 / 去水印(gpt-image edit)+ 裁剪压缩(离线 Pillow)。

计费/错误范式同 main.py:charge_for 预扣 → 读图失败或 AI 失败 → refund + HTTPException。
"""
from __future__ import annotations

import io

from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Depends, BackgroundTasks
from fastapi.responses import JSONResponse
from PIL import Image
from sqlalchemy.orm import Session

from .. import storage
from ..ai.openai_image import OpenAIImageClient
from ..config import settings
from ..db import get_db
from ..models_db import User
from ..services.billing import charge_for, refund
from ..services.image_tools import compress_image
from ..services.jobs import submit_ai_job, save_asset_in_background
from ..services.library import save_as_asset
from ..ai.upscale import get_upscale_provider
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


def _edit_endpoint(raw: bytes, prompt: str, size: str, db: Session, user: User, offline=None) -> str:
    """读图 → 有 key 走 gpt-image,无 key 走本地引擎 offline(真实处理,不报错)→ 落盘。"""
    from ..config import settings
    src = _read_image(raw, db, user, "edit")
    try:
        if settings.openai_api_key:
            out_img = OpenAIImageClient().edit(src, prompt, size=size)
        elif offline is not None:
            out_img = offline(src)
        else:
            raise RuntimeError("该功能需配置 AI key")
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        refund(db, user, "edit")
        raise HTTPException(status_code=502, detail="处理失败,请稍后重试") from exc
    job_id = storage.new_job_id()
    out_img.save(storage.output_path(job_id, "result.png"), format="PNG")
    save_as_asset(db, user.id, out_img, "图案处理", storage.output_url(job_id, "result.png"), source="generated")
    return job_id


@router.post("/upscale")
async def upscale(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    scale: float = Form(2.0),
    user: User = Depends(charge_for("process")),
    db: Session = Depends(get_db),
):
    """超分提质(真·放大输入图)。本地 AI 超分(onnx,慢)→ 后台作业;Lanczos(快)→ 同步。"""
    raw = await file.read()
    src = _read_image(raw, db, user, "process")
    scale = max(1.0, min(scale, 4.0))

    if settings.upscale_provider == "onnx":  # AI 超分慢(CPU 几十秒~分钟)→ 后台作业,前端轮询
        uid = user.id
        rgb = src.convert("RGB")

        def _work(jid: str) -> dict:
            out = get_upscale_provider().upscale(rgb, scale=scale)
            out.save(storage.output_path(jid, "upscaled.png"), format="PNG")
            url = storage.output_url(jid, "upscaled.png")
            save_asset_in_background(uid, out, "超分提质", url)
            return {"image_url": url, "width": out.width, "height": out.height}

        jid = submit_ai_job(background_tasks, db, "upscale", uid, _work, refund_op="process")
        return JSONResponse({"job_id": jid, "status": "pending"})

    # Lanczos:快,同步(原逻辑)
    try:
        out = get_upscale_provider().upscale(src.convert("RGB"), scale=scale)
    except Exception as exc:  # noqa: BLE001
        refund(db, user, "process")
        raise HTTPException(status_code=500, detail="超分失败") from exc
    job_id = storage.new_job_id()
    out.save(storage.output_path(job_id, "upscaled.png"), format="PNG")
    url = storage.output_url(job_id, "upscaled.png")
    save_as_asset(db, user.id, out, "超分提质", url, source="generated")
    return JSONResponse({"job_id": job_id, "image_url": url, "width": out.width, "height": out.height})


@router.post("/expand")
async def expand(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    prompt: str = Form(""),
    size: str = Form("auto"),
    user: User = Depends(charge_for("edit")),
    db: Session = Depends(get_db),
):
    """扩图(outpaint):有 key 走 gpt-image(后台作业),无 key 走本地离线引擎。"""
    raw = await file.read()
    full_prompt = f"{_EXPAND_PROMPT} {prompt}".strip()
    from ..services.effects import outpaint_reflect
    if settings.openai_api_key:  # gpt-image 耗时 -> 后台作业,前端轮询
        src = _read_image(raw, db, user, "edit")
        uid = user.id

        def _work(jid: str) -> dict:
            out_img = OpenAIImageClient().edit(src, full_prompt, size=size)
            out_img.save(storage.output_path(jid, "result.png"), format="PNG")
            url = storage.output_url(jid, "result.png")
            save_asset_in_background(uid, out_img, "图案处理", url)
            return {"image_url": url}

        jid = submit_ai_job(background_tasks, db, "expand", uid, _work, refund_op="edit")
        return JSONResponse({"job_id": jid, "status": "pending"})
    job_id = _edit_endpoint(raw, full_prompt, size, db, user, offline=lambda im: outpaint_reflect(im, 1.5))
    return JSONResponse({"job_id": job_id, "image_url": storage.output_url(job_id, "result.png")})


@router.post("/dewatermark")
async def dewatermark(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    prompt: str = Form(""),
    size: str = Form("auto"),
    user: User = Depends(charge_for("edit")),
    db: Session = Depends(get_db),
):
    """去水印:有 key 走 gpt-image(后台作业),无 key 走本地离线引擎。"""
    raw = await file.read()
    full_prompt = f"{_DEWATERMARK_PROMPT} {prompt}".strip()
    from ..services.effects import dewatermark as _dw
    if settings.openai_api_key:  # gpt-image 耗时 -> 后台作业,前端轮询
        src = _read_image(raw, db, user, "edit")
        uid = user.id

        def _work(jid: str) -> dict:
            out_img = OpenAIImageClient().edit(src, full_prompt, size=size)
            out_img.save(storage.output_path(jid, "result.png"), format="PNG")
            url = storage.output_url(jid, "result.png")
            save_asset_in_background(uid, out_img, "图案处理", url)
            return {"image_url": url}

        jid = submit_ai_job(background_tasks, db, "dewatermark", uid, _work, refund_op="edit")
        return JSONResponse({"job_id": jid, "status": "pending"})
    job_id = _edit_endpoint(raw, full_prompt, size, db, user, offline=_dw)
    return JSONResponse({"job_id": job_id, "image_url": storage.output_url(job_id, "result.png")})


@router.post("/compress")
def compress(
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
    raw = file.file.read()
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
    save_as_asset(db, user.id, _out_img, "裁剪压缩", storage.output_url(job_id, name),
                  source="generated", size_bytes=info["output_bytes"])

    return JSONResponse({
        "job_id": job_id,
        "image_url": storage.output_url(job_id, name),
        "original_bytes": original_bytes,
        "output_bytes": info["output_bytes"],
        "width": info["width"],
        "height": info["height"],
        "format": info["format"],
    })
