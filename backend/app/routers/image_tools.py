"""图案处理工具路由:图像提质(本地超分)/ 去水印(gpt-image edit)。

计费/错误范式同 main.py:charge_for 预扣 → 读图失败或 AI 失败 → refund + HTTPException。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from .. import storage
from ..ai.openai_image import OpenAIImageClient
from ..ai.upscale import get_upscale_provider
from ..config import settings
from ..db import get_db
from ..models_db import User
from ..services.billing import charge_for, refund
from ..services.library import save_as_asset
from ..tasks import run_tool
from ..web_utils import read_image_or_refund as _read_image
from ..web_utils import submit_celery

router = APIRouter(prefix="/api/image-tools", tags=["image-tools"])

_DEWATERMARK_PROMPT = (
    "Remove any watermarks, logos, text overlays and signatures from this image. "
    "Cleanly reconstruct the underlying content so the result looks natural and "
    "watermark-free, keeping the main subject and style intact."
)

# 图像提质「目标分辨率」→ 目标长边像素。换算成放大倍数的事放在 router 做(此处已读图、知尺寸),
# 下游 worker / 超分 provider 仍只吃 scale,不必改。4K=4096 同时是输出长边天花板(防超大)。
_RES_TARGETS = {"1k": 1024, "2k": 2048, "4k": 4096}


def _resolve_scale(size: tuple[int, int], target: str, scale: float) -> float:
    """目标分辨率(1k/2k/4k=长边像素)→ 放大倍数;已 ≥ 目标则不缩小(取 1.0)。
    target=none/未知 → 回退旧的「放大倍数」(封顶 4x)。"""
    t = (target or "none").lower()
    if t in _RES_TARGETS:
        long_edge = max(size) or 1
        return max(1.0, _RES_TARGETS[t] / long_edge)   # 目标 ≤4096,天然封顶;不缩小
    return max(1.0, min(scale, 4.0))


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
    file: UploadFile = File(...),
    target: str = Form("none"),     # 目标分辨率:none(仅提质不放大) | 1k | 2k | 4k(长边像素)
    scale: float = Form(1.0),       # 旧版「放大倍数」(target=none/未知 时生效,兼容旧前端)
    user: User = Depends(charge_for("process")),
    db: Session = Depends(get_db),
):
    """图像提质:本地 AI 超分(Real-ESRGAN)去噪 + 复原细节。
    目标分辨率(1K/2K/4K=长边像素)在此换算成放大倍数;none=仅提质不放大;已 ≥ 目标则不缩小。
    AI 提质 ~几秒 → 后台作业;pillow → 同步;无模型自动降级 Lanczos。
    """
    raw = await file.read()
    src = _read_image(raw, db, user, "process")
    scale = _resolve_scale(src.size, target, scale)

    if settings.upscale_provider == "realesrgan":  # AI 提质 ~几秒 → Celery 后台作业,前端轮询
        return JSONResponse(submit_celery(run_tool, db, user, kind="upscale", tool_id="upscale",
                                          op="process", raw=raw, params={"scale": scale}))

    # pillow(Lanczos):快,同步
    try:
        out = get_upscale_provider().upscale(src.convert("RGB"), scale=scale)
    except Exception as exc:  # noqa: BLE001
        refund(db, user, "process")
        raise HTTPException(status_code=500, detail="提质失败") from exc
    job_id = storage.new_job_id()
    out.save(storage.output_path(job_id, "upscaled.png"), format="PNG")
    url = storage.output_url(job_id, "upscaled.png")
    save_as_asset(db, user.id, out, "提质", url, source="generated")
    return JSONResponse({"job_id": job_id, "image_url": url, "width": out.width, "height": out.height})


@router.post("/dewatermark")
async def dewatermark(
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
    if settings.openai_api_key:  # gpt-image 耗时 -> Celery 后台作业,前端轮询
        _read_image(raw, db, user, "edit")  # 读图失败 → 400 + 退点
        return JSONResponse(submit_celery(run_tool, db, user, kind="dewatermark", tool_id="dewatermark",
                                          op="edit", raw=raw, params={"prompt": full_prompt, "size": size}))
    job_id = _edit_endpoint(raw, full_prompt, size, db, user, offline=_dw)
    return JSONResponse({"job_id": job_id, "image_url": storage.output_url(job_id, "result.png")})
