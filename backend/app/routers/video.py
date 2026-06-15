"""视频生成路由:商品展示动态视频(GIF)。前缀 /api/video。"""
from __future__ import annotations

import io

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from PIL import Image
from sqlalchemy.orm import Session

from .. import storage
from ..ai.video import ASPECT_RATIOS, CATEGORIES, LANGUAGES, RESOLUTION_SHORT
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
        "aspects": list(ASPECT_RATIOS),
        "resolutions": list(RESOLUTION_SHORT),
        "languages": LANGUAGES,
        "categories": CATEGORIES,
        "seconds": settings.video_seconds,
        "ai_ready": settings.video_provider != "local" and bool(settings.video_api_key),
    }


@router.post("/ai-generate")
def ai_generate(
    file: UploadFile = File(...),
    file2: UploadFile | None = File(None),
    prompt: str = Form(""),          # 视频描述/镜头脚本(由前端「视频类型」填入、可自定义编辑)
    language: str = Form("葡萄牙语"),  # 配音/对白语言(默认葡语)
    category: str = Form("通用"),     # 商品类目:决定专属动作序列 + 场景首帧的场景
    scene_frame: bool = Form(False),  # 两步:先 gpt-image 生成场景首帧再生视频(缓解硬切;无 key 自动跳过)
    aspect: str = Form("portrait"),
    resolution: str = Form("1080p"),
    user: User = Depends(charge_for("video")),
    db: Session = Depends(get_db),
):
    """图生视频(AI):1 张图=让它动起来 / 2 张图=首尾帧过渡。视频描述 + 商品标题 + 语言 + 画幅 + 分辨率。
    异步,扣 video=3,失败退点。Provider 由 POD_VIDEO_PROVIDER 决定:默认 local→兜底 GIF;
    设 cogvideox + 填 key→智谱 CogVideoX-3 真视频。画幅按比例等比贴合上传图(防生硬拉伸)。"""
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
    if aspect not in ASPECT_RATIOS:
        aspect = "portrait"
    if resolution not in RESOLUTION_SHORT:
        resolution = "1080p"
    if category not in CATEGORIES:
        category = "通用"
    return submit_celery(
        run_tool, db, user, kind="aivideo", tool_id="videogen", op="video",
        raw=img1, mask_raw=img2,
        params={"prompt": prompt[:2000], "language": language[:20],
                "category": category, "scene_frame": bool(scene_frame),
                "aspect": aspect, "resolution": resolution, "frames2": bool(img2)},
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
