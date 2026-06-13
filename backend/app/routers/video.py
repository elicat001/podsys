"""视频生成路由:商品展示动态视频(GIF)。前缀 /api/video。"""
from __future__ import annotations

import io

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from PIL import Image
from sqlalchemy.orm import Session

from .. import storage
from ..ai.video import ASPECT_SIZE
from ..auth import current_user
from ..config import settings
from ..db import get_db
from ..models_db import User
from ..services import video as video_svc
from ..services.billing import charge_for, refund
from ..tasks import run_tool
from ..web_utils import read_image_or_refund, submit_celery

router = APIRouter(prefix="/api/video", tags=["video"])


@router.get("/options")
def options(user: User = Depends(current_user)):
    # ai_ready=true 表示已配好真 AI 视频(否则后端会兜底成本地 GIF)。前端据此提示用户。
    return {
        "aspects": list(ASPECT_SIZE),
        "seconds": settings.video_seconds,
        "ai_ready": settings.video_provider != "local" and bool(settings.video_api_key),
    }


@router.post("/ai-generate")
def ai_generate(
    file: UploadFile = File(...),
    file2: UploadFile | None = File(None),
    prompt: str = Form(""),
    title: str = Form(""),          # 商品标题(可选):给模型语义锚点,商品显示更稳
    aspect: str = Form("portrait"),
    user: User = Depends(charge_for("video")),
    db: Session = Depends(get_db),
):
    """图生视频(AI):1 张图=让它动起来 / 2 张图=首尾帧过渡;配文字描述运动。异步,扣 video=3,失败退点。
    不暴露分辨率(按画幅用高分辨率)。Provider 由 POD_VIDEO_PROVIDER 决定:
    默认 local→兜底 GIF;设 cogvideox + 填 key→智谱 CogVideoX-3 真视频。"""
    img1 = file.file.read()
    read_image_or_refund(img1, db, user, "video")   # 第 1 张必填;坏图自动退点 + 400
    img2 = None
    if file2 is not None:
        b = file2.file.read()
        try:
            Image.open(io.BytesIO(b)).verify()
            img2 = b
        except Exception:  # noqa: BLE001 — 第 2 张坏图忽略(降级为单图,不阻断)
            img2 = None
    if aspect not in ASPECT_SIZE:
        aspect = "portrait"
    return submit_celery(
        run_tool, db, user, kind="aivideo", tool_id="videogen", op="video",
        raw=img1, mask_raw=img2,
        params={"prompt": prompt[:2000], "title": title[:200], "aspect": aspect, "frames2": bool(img2)},
    )


@router.post("/generate")
def generate(
    file: UploadFile = File(...),
    file2: UploadFile | None = File(None),
    style: str = Form("kenburns"),
    aspect: str = Form("square"),
    fps: int = Form(12),
    text: str = Form(""),
    user: User = Depends(charge_for("video")),
    db: Session = Depends(get_db),
):
    """从 1~2 张图生成商品展示 GIF(扣 video=3)。失败退点。"""
    images = [read_image_or_refund(file.file.read(), db, user, "video")]
    if file2 is not None:
        try:
            im2 = Image.open(io.BytesIO(file2.file.read())); im2.load()
            images.append(im2)
        except Exception:  # noqa: BLE001  第二张坏图忽略,不阻断
            pass
    try:
        result = video_svc.make_showcase(images, style=style, aspect=aspect, fps=fps, text=text)
    except Exception as exc:  # noqa: BLE001
        refund(db, user, "video")
        raise HTTPException(status_code=500, detail="视频生成失败") from exc

    job_id = storage.new_job_id()
    storage.output_path(job_id, "showcase.gif").write_bytes(result["bytes"])
    return {
        "job_id": job_id,
        "video_url": storage.output_url(job_id, "showcase.gif"),
        "frames": result["frames"],
        "width": result["width"],
        "height": result["height"],
        "duration_ms": result["duration_ms"],
    }
