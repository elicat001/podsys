"""视频生成路由:商品展示动态视频(GIF)。前缀 /api/video。"""
from __future__ import annotations
import io
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from PIL import Image
from sqlalchemy.orm import Session
from ..db import get_db
from ..models_db import User
from ..auth import current_user
from ..services.billing import charge_for, refund
from ..services import video as video_svc
from ..web_utils import read_image_or_refund
from .. import storage

router = APIRouter(prefix="/api/video", tags=["video"])


@router.get("/options")
def options(user: User = Depends(current_user)):
    return {"aspects": list(video_svc.ASPECTS), "styles": ["kenburns", "slideshow"]}


@router.post("/generate")
async def generate(
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
    images = [read_image_or_refund(await file.read(), db, user, "video")]
    if file2 is not None:
        try:
            im2 = Image.open(io.BytesIO(await file2.read())); im2.load()
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
