"""图案处理工具路由:扩图 / 去水印(gpt-image edit)+ 裁剪压缩(离线 Pillow)。

计费/错误范式同 main.py:charge_for 预扣 → 读图失败或 AI 失败 → refund + HTTPException。
"""
from __future__ import annotations


from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from .. import storage
from ..ai.openai_image import OpenAIImageClient
from ..config import settings
from ..db import get_db
from ..models_db import User
from ..services.billing import charge_for, refund
from ..services.library import save_as_asset
from ..ai.upscale import get_upscale_provider
from ..tasks import run_tool
from ..web_utils import read_image_or_refund as _read_image
from ..web_utils import submit_celery

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
    file: UploadFile = File(...),
    scale: float = Form(1.0),
    user: User = Depends(charge_for("process")),
    db: Session = Depends(get_db),
):
    """图像提质:本地 AI 超分(Real-ESRGAN)去噪 + 复原细节,**默认保持原尺寸不放大**(scale=1);
    scale>1 时放大。AI 提质 ~几秒 → 后台作业;pillow → 同步;无模型自动降级 Lanczos。
    """
    raw = await file.read()
    src = _read_image(raw, db, user, "process")
    scale = max(1.0, min(scale, 4.0))

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


@router.post("/expand")
async def expand(
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
    if settings.openai_api_key:  # gpt-image 耗时 -> Celery 后台作业,前端轮询
        _read_image(raw, db, user, "edit")  # 读图失败 → 400 + 退点
        return JSONResponse(submit_celery(run_tool, db, user, kind="expand", tool_id="expand",
                                          op="edit", raw=raw, params={"prompt": full_prompt, "size": size}))
    job_id = _edit_endpoint(raw, full_prompt, size, db, user, offline=lambda im: outpaint_reflect(im, 1.5))
    return JSONResponse({"job_id": job_id, "image_url": storage.output_url(job_id, "result.png")})


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
    """裁剪压缩(纯离线 Pillow,op=process 扣 2)→ Celery 后台作业,前端轮询。

    结果带原始/压缩后字节数 + 尺寸/格式(worker 内算,见 tasks._work_compress)。
    """
    raw = file.file.read()
    _read_image(raw, db, user, "process")  # 读图失败 → 400 + 退点
    # 参数非法同步早拦截(400),不进后台:超大目标尺寸 / 非法格式 / 质量越界
    from ..services.image_tools import MAX_TARGET_PIXELS
    if target_w and target_h and target_w * target_h > MAX_TARGET_PIXELS:
        refund(db, user, "process")
        raise HTTPException(status_code=400, detail="目标尺寸过大(像素数超上限)")
    if fmt not in ("png", "jpeg", "webp") or not (1 <= quality <= 100):
        refund(db, user, "process")
        raise HTTPException(status_code=400, detail="格式或质量参数非法")
    return JSONResponse(submit_celery(
        run_tool, db, user, kind="compress", tool_id="compress", op="process", raw=raw,
        params={"target_w": target_w, "target_h": target_h, "quality": quality, "fmt": fmt}))
