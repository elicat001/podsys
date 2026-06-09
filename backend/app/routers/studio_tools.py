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
from ..config import settings
from ..db import get_db
from ..models_db import User
from ..services import studio_tools
from ..services.billing import InsufficientCredits, charge, charge_for, refund
from ..tasks import run_tool
from ..web_utils import read_image_or_refund as _read
from ..web_utils import submit_celery

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
    engine: str = Form("auto"),   # ai=智能(识图 SEO,需 key,扣1)| fast=快速(本地规则,免费)| auto=有 key 走 AI
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """标题提取。智能(AI 识图,扣 1 点);快速(本地规则,不扣点)。"""
    prefer_local = engine == "fast"
    if engine == "ai" and not studio_tools.has_openai_key():
        raise HTTPException(status_code=502, detail="智能运行需配置 AI key;可改用「快速运行」(本地)")
    has_key = studio_tools.has_openai_key() and not prefer_local
    # 可选图片:本地引擎用其主色调 / AI 识图(损坏/未传则忽略)
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
        # 有 key 且非快速才扣点(余额不足 -> charge 内部抛 InsufficientCredits)
        try:
            charge(db, user, "title")
            charged = True
        except InsufficientCredits as exc:
            raise HTTPException(status_code=402, detail=str(exc)) from exc

    try:
        result = studio_tools.generate_title(keywords=keywords, category=category, img=img,
                                             prefer_local=prefer_local)
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
    file: UploadFile = File(...),
    size: str = Form("auto"),
    user: User = Depends(charge_for("edit")),
    db: Session = Depends(get_db),
):
    """模特试衣:服饰印花图 -> 模特上身图。无 key -> 502 + 退点。"""
    raw = await file.read()
    garment = _read(raw, db, user, "edit")
    if settings.openai_api_key:  # gpt-image 耗时 -> Celery 后台作业,前端轮询
        return submit_celery(run_tool, db, user, kind="tryon", tool_id="tryon", op="edit",
                             raw=raw, params={"size": size})
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
    raw = await file.read()
    pet = _read(raw, db, user, "edit")
    if settings.openai_api_key:  # gpt-image 耗时 -> Celery 后台作业,前端轮询
        return submit_celery(run_tool, db, user, kind="pet-costume", tool_id="pet", op="edit",
                             raw=raw, params={"costume": costume, "size": size})
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
    raw = await file.read()
    base = _read(raw, db, user, "edit")
    if settings.openai_api_key:  # gpt-image 耗时 -> Celery 后台作业,前端轮询
        return submit_celery(run_tool, db, user, kind="group-photo", tool_id="group", op="edit",
                             raw=raw, params={"prompt": prompt, "size": size})
    try:
        out = studio_tools.group_photo(base, prompt, size=size)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        refund(db, user, "edit")
        raise HTTPException(status_code=502, detail="合照生成失败,请稍后重试") from exc
    return _save(out, "group.png")
