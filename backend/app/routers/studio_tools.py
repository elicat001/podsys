"""套图&标题&来图定制路由(E3)。前缀 /api/studio。

- /title       标题提取(gpt 文本):**仅有 key 时扣点**,无 key 降级 200 不扣点。
- /tryon       模特试衣(gpt-image edit,charge_for("edit"))
- /pet-costume 宠物换装(gpt-image edit)
- /group-photo 合照(gpt-image edit)

gpt-image 端点沿用「charge_for 预扣 -> 失败 refund 退点 -> 502」范式。
"""
from __future__ import annotations

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
from ..services.jobs import create_job
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
    """标题提取 → Celery 后台作业(提交即走、任务中心看)。智能(AI 识图扣 1;降级则退);快速(本地规则免费)。"""
    if engine == "ai" and not studio_tools.has_openai_key():
        raise HTTPException(status_code=502, detail="智能运行需配置 AI key;可改用「快速运行」(本地)")
    eng = "ai" if (engine == "ai" or (engine == "auto" and studio_tools.has_openai_key())) else "fast"

    # 智能(ai)预扣 1 点;快速免费。降级/失败的退点在 worker(_work_title)内处理。
    if eng == "ai":
        try:
            charge(db, user, "title")
        except InsufficientCredits as exc:
            raise HTTPException(status_code=402, detail=str(exc)) from exc

    raw = None  # 可选辅助图
    if file is not None:
        try:
            r = await file.read()
            raw = r or None
        except Exception:  # noqa: BLE001
            raw = None

    job = create_job(db, "title", owner_id=user.id, tool_id="title",
                     params={"keywords": keywords, "category": category, "engine": eng})
    if raw is not None:
        storage.upload_path(job.id).write_bytes(raw)
    try:
        run_tool.delay(job.id)
    except Exception as exc:  # noqa: BLE001 — broker 挂了:智能退点 + 502
        if eng == "ai":
            refund(db, user, "title")
        job.status = "error"; job.error = f"队列不可用: {type(exc).__name__}"; db.commit()
        raise HTTPException(status_code=502, detail="后台队列暂时不可用,请稍后重试(点数已退回)") from exc
    return {"job_id": job.id, "status": "pending"}


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
