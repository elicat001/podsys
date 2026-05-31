"""套图&标题&来图定制路由(E3)。前缀 /api/studio。

- /title       标题提取(gpt 文本):**仅有 key 时扣点**,无 key 降级 200 不扣点。
- /tryon       模特试衣(gpt-image edit,charge_for("edit"))
- /pet-costume 宠物换装(gpt-image edit)
- /group-photo 合照(gpt-image edit)

gpt-image 端点沿用「charge_for 预扣 -> 失败 refund 退点 -> 502」范式。
"""
from __future__ import annotations

import io

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from PIL import Image
from sqlalchemy.orm import Session

from .. import storage
from ..auth import current_user
from ..db import get_db
from ..models_db import User
from ..services import studio_tools
from ..services.billing import InsufficientCredits, charge, charge_for, refund
from ..web_utils import read_image_or_refund as _read

router = APIRouter(prefix="/api/studio", tags=["studio"])


def _save(img: Image.Image, name: str = "studio.png") -> dict:
    job_id = storage.new_job_id()
    img.save(storage.output_path(job_id, name), format="PNG")
    return {"job_id": job_id, "image_url": storage.output_url(job_id, name)}


@router.post("/title")
def title(
    keywords: str = Form(""),
    category: str = Form("apparel"),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """标题提取。无 key:降级占位、**不扣点**;有 key:扣 5 点调文本模型,失败退点。"""
    has_key = studio_tools.has_openai_key()
    charged = False
    if has_key:
        # 有 key 才扣点(余额不足 -> charge 内部抛 InsufficientCredits)
        try:
            charge(db, user, "generate")
            charged = True
        except InsufficientCredits as exc:
            raise HTTPException(status_code=402, detail=str(exc)) from exc

    try:
        result = studio_tools.generate_title(keywords=keywords, category=category)
    except Exception as exc:  # noqa: BLE001
        if charged:
            refund(db, user, "generate")
        raise HTTPException(status_code=502, detail="标题生成失败,请稍后重试") from exc
    return result


@router.post("/tryon")
async def tryon(
    file: UploadFile = File(...),
    size: str = Form("auto"),
    user: User = Depends(charge_for("edit")),
    db: Session = Depends(get_db),
):
    """模特试衣:服饰印花图 -> 模特上身图。无 key -> 502 + 退点。"""
    garment = _read(await file.read(), db, user, "edit")
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
    file: UploadFile = File(...),
    costume: str = Form("royal european"),
    size: str = Form("auto"),
    user: User = Depends(charge_for("edit")),
    db: Session = Depends(get_db),
):
    """宠物换装。无 key -> 502 + 退点。"""
    pet = _read(await file.read(), db, user, "edit")
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
    file: UploadFile = File(...),
    prompt: str = Form(...),
    size: str = Form("auto"),
    user: User = Depends(charge_for("edit")),
    db: Session = Depends(get_db),
):
    """合照。无 key -> 502 + 退点。"""
    base = _read(await file.read(), db, user, "edit")
    try:
        out = studio_tools.group_photo(base, prompt, size=size)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        refund(db, user, "edit")
        raise HTTPException(status_code=502, detail="合照生成失败,请稍后重试") from exc
    return _save(out, "group.png")
