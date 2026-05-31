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
from ..db import get_db
from ..models_db import User
from ..auth import current_user
from ..services.billing import charge_for, charge, refund, InsufficientCredits
from ..services import design_tools

router = APIRouter(prefix="/api/design-tools", tags=["design-tools"])


def _read_or_refund(raw: bytes, db: Session, user: User) -> Image.Image:
    """读图失败 -> 退点 + 400。"""
    try:
        im = Image.open(io.BytesIO(raw))
        im.load()
        return im
    except Exception as exc:  # noqa: BLE001
        refund(db, user, "edit")
        raise HTTPException(status_code=400, detail=f"无法读取图片: {exc}") from exc


@router.post("/variants")
async def variants(
    file: UploadFile = File(...),
    n: int = Form(3),
    prompt: str = Form(""),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """图裂变:生成 N 个爆款变体。按张计费(P0-1:n 次 AI 调用 = n 次扣点),
    任意失败则把已扣的 n 笔全部退回,保证扣点笔数与退点笔数对齐。"""
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

    try:
        src = Image.open(io.BytesIO(await file.read())); src.load()
    except Exception as exc:  # noqa: BLE001
        _refund_all()
        raise HTTPException(status_code=400, detail=f"无法读取图片: {exc}") from exc

    try:
        imgs = design_tools.make_variants(src, n, prompt=prompt)
    except Exception as exc:  # noqa: BLE001
        _refund_all()
        raise HTTPException(status_code=502, detail="图裂变失败,请稍后重试") from exc

    job_id = storage.new_job_id()
    urls = []
    for i, im in enumerate(imgs):
        name = f"variant_{i + 1}.png"
        im.save(storage.output_path(job_id, name), format="PNG")
        urls.append(storage.output_url(job_id, name))
    return {"job_id": job_id, "images": urls}


@router.post("/fuse")
async def fuse(
    file: UploadFile = File(...),
    prompt: str = Form(...),
    user: User = Depends(charge_for("edit")),
    db: Session = Depends(get_db),
):
    """元素融合:把输入图与 prompt 融合出新爆款。"""
    src = _read_or_refund(await file.read(), db, user)
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
    return {"job_id": job_id, "image_url": storage.output_url(job_id, name)}


@router.post("/restyle")
async def restyle(
    file: UploadFile = File(...),
    style: str = Form(...),
    user: User = Depends(charge_for("edit")),
    db: Session = Depends(get_db),
):
    """风格转绘:按目标风格(如 Temu 2D flat)重绘。"""
    src = _read_or_refund(await file.read(), db, user)
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
    return {"job_id": job_id, "image_url": storage.output_url(job_id, name)}


@router.post("/meme")
async def meme(
    file: UploadFile = File(...),
    text: str = Form(...),
    prompt: str = Form(""),
    user: User = Depends(charge_for("edit")),
    db: Session = Depends(get_db),
):
    """梗图印花:加梗文案/排版。"""
    src = _read_or_refund(await file.read(), db, user)
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
    return {"job_id": job_id, "image_url": storage.output_url(job_id, name)}
