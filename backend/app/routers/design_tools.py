"""印花设计工具路由(E1):/api/design-tools。

每个端点:接 multipart 图,`charge_for("edit")` 预扣 4 点;读图失败 400+退点;
gpt-image 调用失败 refund + 502(不透传内部异常原文)。产物存 storage.output_path。
"""
from __future__ import annotations

import io

from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Depends
from PIL import Image
from sqlalchemy.orm import Session

from .. import storage
from ..config import settings
from ..db import get_db
from ..models_db import User
from ..auth import current_user
from ..services.billing import charge_for, charge, refund, InsufficientCredits
from ..services import design_tools
from ..services.library import save_as_asset
from ..tasks import run_tool
from ..web_utils import read_image_or_refund, submit_celery

router = APIRouter(prefix="/api/design-tools", tags=["design-tools"])


def _read_or_refund(raw: bytes, db: Session, user: User) -> Image.Image:
    return read_image_or_refund(raw, db, user, "edit")


@router.post("/variants")
async def variants(
    file: UploadFile = File(...),
    n: int = Form(3),
    prompt: str = Form(""),
    engine: str = Form("auto"),   # ai=智能(gpt)| fast=快速(本地换色)| auto=有 key 走 AI 否则本地
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """图裂变:生成 N 个爆款变体。按张计费(P0-1:n 次 = n 次扣点),任意失败全退,笔数对齐。"""
    if n < 1 or n > 6:
        raise HTTPException(status_code=400, detail="n 必须在 1~6 之间")

    def _refund_all() -> None:
        for _ in range(n):
            refund(db, user, "edit")

    # 按张预扣 n 次
    charged = 0
    try:
        for _ in range(n):
            charge(db, user, "edit")
            charged += 1
    except InsufficientCredits as exc:
        for _ in range(charged):
            refund(db, user, "edit")
        raise HTTPException(status_code=402, detail=str(exc)) from exc

    raw = await file.read()
    try:
        Image.open(io.BytesIO(raw)).load()
    except Exception as exc:  # noqa: BLE001
        _refund_all()
        raise HTTPException(status_code=400, detail=f"无法读取图片: {exc}") from exc

    if engine == "ai" and not settings.openai_api_key:
        _refund_all()
        raise HTTPException(status_code=502, detail="智能运行需配置 AI key;可改用「快速运行」(本地)")
    # 一律 Celery 后台作业(提交即走、任务中心看结果);engine 决定 worker 用 AI 还是本地换色。
    eng = "ai" if (engine == "ai" or (engine == "auto" and settings.openai_api_key)) else "fast"
    return submit_celery(run_tool, db, user, kind="variants", tool_id="variants", op="edit",
                         raw=raw, params={"n": n, "prompt": prompt, "engine": eng}, n=n)


@router.post("/seamless")
def seamless(
    file: UploadFile = File(...),
    repeat: int = Form(2),
    user: User = Depends(charge_for("process")),
    db: Session = Depends(get_db),
):
    """四方连续图(离线 Pillow,op=process 扣 2)→ Celery 后台作业,前端轮询。"""
    raw = file.file.read()
    read_image_or_refund(raw, db, user, "process")  # 读图失败 → 400 + 退点
    if not (1 <= repeat <= 8):  # 参数非法同步早拦截(400),不进后台
        refund(db, user, "process")
        raise HTTPException(status_code=400, detail="repeat 必须在 1~8 之间")
    return submit_celery(run_tool, db, user, kind="seamless", tool_id="seamless", op="process",
                         raw=raw, params={"repeat": repeat})


@router.post("/fuse")
async def fuse(
    file: UploadFile = File(...),
    prompt: str = Form(...),
    user: User = Depends(charge_for("edit")),
    db: Session = Depends(get_db),
):
    """元素融合:把输入图与 prompt 融合出新爆款。"""
    raw = await file.read()
    src = _read_or_refund(raw, db, user)
    if settings.openai_api_key:
        return submit_celery(run_tool, db, user, kind="fuse", tool_id="fuse", op="edit",
                             raw=raw, params={"prompt": prompt})
    try:
        out_img = design_tools.make_fuse(src, prompt)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        refund(db, user, "edit")
        raise HTTPException(status_code=502, detail="元素融合失败,请稍后重试") from exc

    job_id = storage.new_job_id()
    name = "fused.png"
    out_img.save(storage.output_path(job_id, name), format="PNG")
    url = storage.output_url(job_id, name)
    save_as_asset(db, user.id, out_img, "元素融合", url, source="generated")
    return {"job_id": job_id, "image_url": url}


@router.post("/restyle")
async def restyle(
    file: UploadFile = File(...),
    style: str = Form(...),
    user: User = Depends(charge_for("edit")),
    db: Session = Depends(get_db),
):
    """风格转绘:按目标风格(如 Temu 2D flat)重绘。"""
    raw = await file.read()
    src = _read_or_refund(raw, db, user)
    if settings.openai_api_key:
        return submit_celery(run_tool, db, user, kind="restyle", tool_id="restyle", op="edit",
                             raw=raw, params={"style": style})
    try:
        out_img = design_tools.make_restyle(src, style)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        refund(db, user, "edit")
        raise HTTPException(status_code=502, detail="风格转绘失败,请稍后重试") from exc

    job_id = storage.new_job_id()
    name = "restyled.png"
    out_img.save(storage.output_path(job_id, name), format="PNG")
    url = storage.output_url(job_id, name)
    save_as_asset(db, user.id, out_img, f"风格转绘: {style[:20]}", url, source="generated")
    return {"job_id": job_id, "image_url": url}


@router.post("/meme")
async def meme(
    file: UploadFile = File(...),
    text: str = Form(""),  # 留空 → 自动看图生成有梗文案(meme_prompt 内分流)
    prompt: str = Form(""),
    user: User = Depends(charge_for("edit")),
    db: Session = Depends(get_db),
):
    """梗图印花:加梗文案/排版。"""
    raw = await file.read()
    src = _read_or_refund(raw, db, user)
    if settings.openai_api_key:
        return submit_celery(run_tool, db, user, kind="meme", tool_id="meme", op="edit",
                             raw=raw, params={"text": text, "prompt": prompt})
    try:
        out_img = design_tools.make_meme(src, text, prompt=prompt)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        refund(db, user, "edit")
        raise HTTPException(status_code=502, detail="梗图印花失败,请稍后重试") from exc

    job_id = storage.new_job_id()
    name = "meme.png"
    out_img.save(storage.output_path(job_id, name), format="PNG")
    url = storage.output_url(job_id, name)
    save_as_asset(db, user.id, out_img, "梗图印花", url, source="generated")
    return {"job_id": job_id, "image_url": url}
