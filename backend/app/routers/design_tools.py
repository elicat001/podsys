"""印花设计工具路由(E1):/api/design-tools。

每个端点:接 multipart 图,`charge_for("edit")` 预扣 4 点;读图失败 400+退点;
gpt-image 调用失败 refund + 502(不透传内部异常原文)。产物存 storage.output_path。
"""
from __future__ import annotations

import io

from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Depends, BackgroundTasks
from PIL import Image
from sqlalchemy.orm import Session

from .. import storage
from ..config import settings
from ..db import get_db
from ..models_db import User
from ..auth import current_user
from ..services.billing import charge_for, charge, refund, InsufficientCredits
from ..services import design_tools
from ..services import seamless as seamless_svc
from ..services.jobs import submit_ai_job, save_asset_in_background
from ..services.library import save_as_asset
from ..web_utils import read_image_or_refund

router = APIRouter(prefix="/api/design-tools", tags=["design-tools"])


def _read_or_refund(raw: bytes, db: Session, user: User) -> Image.Image:
    return read_image_or_refund(raw, db, user, "edit")


@router.post("/variants")
async def variants(
    background_tasks: BackgroundTasks,
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

    # 有 key:gpt-image 耗时(单张数十秒),改后台作业避免 HTTP 超时(Failed to fetch);
    # 立即返回 job_id,前端轮询 /api/jobs/{id}。无 key 走下方离线同步路径(原逻辑不变)。
    if settings.openai_api_key:
        uid = user.id

        def _work(jid: str) -> dict:
            imgs = design_tools.make_variants(src, n, prompt=prompt)
            urls = []
            for i, im in enumerate(imgs):
                name = f"variant_{i + 1}.png"
                im.save(storage.output_path(jid, name), format="PNG")
                url = storage.output_url(jid, name)
                urls.append(url)
                save_asset_in_background(uid, im, f"图裂变 {i + 1}", url)
            return {"images": urls}

        jid = submit_ai_job(background_tasks, db, "variants", uid, _work,
                            refund_op="edit", refund_n=n)
        return {"job_id": jid, "status": "pending"}

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
        url = storage.output_url(job_id, name)
        urls.append(url)
        save_as_asset(db, user.id, im, f"图裂变 {i+1}", url, source="generated")
    return {"job_id": job_id, "images": urls}


@router.post("/seamless")
def seamless(
    file: UploadFile = File(...),
    repeat: int = Form(2),
    user: User = Depends(charge_for("process")),
    db: Session = Depends(get_db),
):
    """四方连续图(离线 Pillow,op=process 扣 2):镜像无缝基块 + 平铺。"""
    src = read_image_or_refund(file.file.read(), db, user, "process")
    try:
        out = seamless_svc.seamless_pattern(src, repeat=repeat)
    except ValueError as exc:
        refund(db, user, "process")
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    job_id = storage.new_job_id()
    out.save(storage.output_path(job_id, "seamless.png"), format="PNG")
    url = storage.output_url(job_id, "seamless.png")
    save_as_asset(db, user.id, out, "四方连续图", url, source="generated")
    return {"job_id": job_id, "image_url": url}


@router.post("/fuse")
async def fuse(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    prompt: str = Form(...),
    user: User = Depends(charge_for("edit")),
    db: Session = Depends(get_db),
):
    """元素融合:把输入图与 prompt 融合出新爆款。"""
    src = _read_or_refund(await file.read(), db, user)
    if settings.openai_api_key:
        uid = user.id

        def _work(jid: str) -> dict:
            out_img = design_tools.make_fuse(src, prompt)
            out_img.save(storage.output_path(jid, "fused.png"), format="PNG")
            url = storage.output_url(jid, "fused.png")
            save_asset_in_background(uid, out_img, "元素融合", url)
            return {"image_url": url}

        jid = submit_ai_job(background_tasks, db, "fuse", uid, _work, refund_op="edit")
        return {"job_id": jid, "status": "pending"}
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
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    style: str = Form(...),
    user: User = Depends(charge_for("edit")),
    db: Session = Depends(get_db),
):
    """风格转绘:按目标风格(如 Temu 2D flat)重绘。"""
    src = _read_or_refund(await file.read(), db, user)
    if settings.openai_api_key:
        uid = user.id

        def _work(jid: str) -> dict:
            out_img = design_tools.make_restyle(src, style)
            out_img.save(storage.output_path(jid, "restyled.png"), format="PNG")
            url = storage.output_url(jid, "restyled.png")
            save_asset_in_background(uid, out_img, f"风格转绘: {style[:20]}", url)
            return {"image_url": url}

        jid = submit_ai_job(background_tasks, db, "restyle", uid, _work, refund_op="edit")
        return {"job_id": jid, "status": "pending"}
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
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    text: str = Form(...),
    prompt: str = Form(""),
    user: User = Depends(charge_for("edit")),
    db: Session = Depends(get_db),
):
    """梗图印花:加梗文案/排版。"""
    src = _read_or_refund(await file.read(), db, user)
    if settings.openai_api_key:
        uid = user.id

        def _work(jid: str) -> dict:
            out_img = design_tools.make_meme(src, text, prompt=prompt)
            out_img.save(storage.output_path(jid, "meme.png"), format="PNG")
            url = storage.output_url(jid, "meme.png")
            save_asset_in_background(uid, out_img, "梗图印花", url)
            return {"image_url": url}

        jid = submit_ai_job(background_tasks, db, "meme", uid, _work, refund_op="edit")
        return {"job_id": jid, "status": "pending"}
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
