"""套图&标题&来图定制路由(E3)。前缀 /api/studio。

- /title       标题提取(gpt 文本):**仅有 key 时扣点**,无 key 降级 200 不扣点。
- /tryon       模特试衣(gpt-image edit,charge_for("edit"))
- /pet-costume 宠物换装(gpt-image edit)
- /group-photo 合照(gpt-image edit)

gpt-image 端点沿用「charge_for 预扣 -> 失败 refund 退点 -> 502」范式。
"""
from __future__ import annotations

import io

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from PIL import Image
from sqlalchemy.orm import Session

from .. import storage
from ..auth import current_user
from ..config import settings
from ..db import get_db
from ..models_db import User
from ..services import studio_tools
from ..services.billing import InsufficientCredits, charge, charge_for, refund
from ..services.jobs import submit_ai_job
from ..web_utils import read_image_or_refund as _read

router = APIRouter(prefix="/api/studio", tags=["studio"])


def _save(img: Image.Image, name: str = "studio.png") -> dict:
    job_id = storage.new_job_id()
    img.save(storage.output_path(job_id, name), format="PNG")
    return {"job_id": job_id, "image_url": storage.output_url(job_id, name)}


@router.post("/title")
async def title(
    keywords: str = Form(""),
    category: str = Form("apparel"),
    file: UploadFile | None = File(None),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """标题提取。**AI 为主路径**(走 key,扣 1 点);**无 key/AI 失败 → 本地规则引擎兜底,不扣点**(退回)。

    可选上传图片:本地兜底时用图的主色调给标题加前缀(Minimalist/具体色名等)。
    """
    has_key = studio_tools.has_openai_key()
    # 可选图片:仅本地降级引擎用其主色调(损坏/未传则忽略,不影响主流程)
    img: Image.Image | None = None
    if file is not None:
        try:
            raw = await file.read()
            if raw:
                img = Image.open(io.BytesIO(raw))
                img.load()
        except Exception:  # noqa: BLE001
            img = None
    charged = False
    if has_key:
        # 有 key 才扣点(余额不足 -> charge 内部抛 InsufficientCredits)
        try:
            charge(db, user, "title")
            charged = True
        except InsufficientCredits as exc:
            raise HTTPException(status_code=402, detail=str(exc)) from exc

    try:
        result = studio_tools.generate_title(keywords=keywords, category=category, img=img)
    except Exception as exc:  # noqa: BLE001
        if charged:
            refund(db, user, "title")
        raise HTTPException(status_code=502, detail="标题生成失败,请稍后重试") from exc
    # 降级到本地兜底(无 key/AI 失败)→ 退回已扣的点,实际 0
    if result.get("degraded") and charged:
        refund(db, user, "title")
    return result


@router.post("/tryon")
async def tryon(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    size: str = Form("auto"),
    user: User = Depends(charge_for("edit")),
    db: Session = Depends(get_db),
):
    """模特试衣:服饰印花图 -> 模特上身图。无 key -> 502 + 退点。"""
    garment = _read(await file.read(), db, user, "edit")
    if settings.openai_api_key:  # gpt-image 耗时 -> 后台作业,前端轮询
        uid = user.id

        def _work(jid: str) -> dict:
            out = studio_tools.model_tryon(garment, size=size)
            out.save(storage.output_path(jid, "tryon.png"), format="PNG")
            return {"image_url": storage.output_url(jid, "tryon.png")}

        jid = submit_ai_job(background_tasks, db, "tryon", uid, _work, refund_op="edit")
        return {"job_id": jid, "status": "pending"}
    try:
        out = studio_tools.model_tryon(garment, size=size)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        refund(db, user, "edit")
        raise HTTPException(status_code=502, detail="模特试衣失败,请稍后重试") from exc
    return _save(out, "tryon.png")


@router.post("/pet-costume")
async def pet_costume_endpoint(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    costume: str = Form("royal european"),
    size: str = Form("auto"),
    user: User = Depends(charge_for("edit")),
    db: Session = Depends(get_db),
):
    """宠物换装。无 key -> 502 + 退点。"""
    pet = _read(await file.read(), db, user, "edit")
    if settings.openai_api_key:  # gpt-image 耗时 -> 后台作业,前端轮询
        uid = user.id

        def _work(jid: str) -> dict:
            out = studio_tools.pet_costume(pet, costume=costume, size=size)
            out.save(storage.output_path(jid, "pet.png"), format="PNG")
            return {"image_url": storage.output_url(jid, "pet.png")}

        jid = submit_ai_job(background_tasks, db, "pet-costume", uid, _work, refund_op="edit")
        return {"job_id": jid, "status": "pending"}
    try:
        out = studio_tools.pet_costume(pet, costume=costume, size=size)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        refund(db, user, "edit")
        raise HTTPException(status_code=502, detail="宠物换装失败,请稍后重试") from exc
    return _save(out, "pet.png")


@router.post("/group-photo")
async def group_photo_endpoint(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    prompt: str = Form(...),
    size: str = Form("auto"),
    user: User = Depends(charge_for("edit")),
    db: Session = Depends(get_db),
):
    """合照。无 key -> 502 + 退点。"""
    base = _read(await file.read(), db, user, "edit")
    if settings.openai_api_key:  # gpt-image 耗时 -> 后台作业,前端轮询
        uid = user.id

        def _work(jid: str) -> dict:
            out = studio_tools.group_photo(base, prompt, size=size)
            out.save(storage.output_path(jid, "group.png"), format="PNG")
            return {"image_url": storage.output_url(jid, "group.png")}

        jid = submit_ai_job(background_tasks, db, "group-photo", uid, _work, refund_op="edit")
        return {"job_id": jid, "status": "pending"}
    try:
        out = studio_tools.group_photo(base, prompt, size=size)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        refund(db, user, "edit")
        raise HTTPException(status_code=502, detail="合照生成失败,请稍后重试") from exc
    return _save(out, "group.png")
