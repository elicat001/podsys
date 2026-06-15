"""团队资源路由:套图模板(MockupTemplate)。前缀 /api/team。

「团队资源」按 `org_id` 共享:同组织成员看到同一批套图模板。管理资源不扣点(current_user 即可)。
一个套图模板 = 多张已印图案的真实产品照;商品套图运行时把每张照片的原印花换成用户新印花。
"""
from __future__ import annotations

import io

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import storage
from ..auth import current_user
from ..db import get_db
from ..models_db import User
from ..models_team import MockupTemplate, MockupTemplateImage

router = APIRouter(prefix="/api/team", tags=["team"])

MAX_IMAGES = 10


def _serialize(t: MockupTemplate) -> dict:
    return {
        "id": t.id,
        "name": t.name,
        "owner_id": t.owner_id,
        "image_count": len(t.images),
        "images": [{"id": im.id, "url": im.path} for im in t.images],
        "created_at": t.created_at.isoformat() if t.created_at else None,
    }


def _own_template(db: Session, tid: int, user: User) -> MockupTemplate:
    """取本组织的套图模板,否则 404(团队资源按 org 共享)。"""
    t = db.get(MockupTemplate, tid)
    if t is None or t.org_id != user.org_id:
        raise HTTPException(status_code=404, detail="套图模板不存在")
    return t


@router.get("/mockup-templates")
def list_templates(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """列出本组织的套图模板(团队共享)。"""
    rows = db.execute(
        select(MockupTemplate).where(MockupTemplate.org_id == user.org_id)
        .order_by(MockupTemplate.created_at.desc())
    ).scalars().all()
    return [_serialize(t) for t in rows]


@router.post("/mockup-templates")
async def create_template(name: str = Form(...), files: list[UploadFile] = File(...),
                          user: User = Depends(current_user), db: Session = Depends(get_db)):
    """新建套图模板:上传 1~MAX_IMAGES 张产品照。逐张校验可解码 + 落盘 + 登记。"""
    name = name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="请填写模板名")
    if not (1 <= len(files) <= MAX_IMAGES):
        raise HTTPException(status_code=400, detail=f"图片数需在 1~{MAX_IMAGES} 之间")

    tpl = MockupTemplate(owner_id=user.id, org_id=user.org_id, name=name)
    db.add(tpl); db.flush()  # 拿到 tpl.id

    job_id = storage.new_job_id()  # 一个目录放该模板的所有产品照
    for i, f in enumerate(files):
        raw = await f.read()
        try:
            Image.open(io.BytesIO(raw)).load()
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            raise HTTPException(status_code=400, detail=f"第 {i + 1} 张图无法读取: {exc}") from exc
        name_i = f"product_{i}.png"
        # 统一转 PNG 落盘(产品照可能是 jpg/webp)
        Image.open(io.BytesIO(raw)).convert("RGB").save(storage.output_path(job_id, name_i), format="PNG")
        db.add(MockupTemplateImage(template_id=tpl.id, path=storage.output_url(job_id, name_i), idx=i))

    db.commit(); db.refresh(tpl)
    storage.mirror_job(job_id)  # 镜像产品照进对象存储(local no-op)
    return _serialize(tpl)


@router.post("/mockup-templates/{tid}/images")
async def add_images(tid: int, files: list[UploadFile] = File(...),
                     user: User = Depends(current_user), db: Session = Depends(get_db)):
    """给已有套图模板追加产品照(总数不超过 MAX_IMAGES)。"""
    tpl = _own_template(db, tid, user)
    cur = len(tpl.images)
    if not files:
        raise HTTPException(status_code=400, detail="请选择要添加的图片")
    if cur + len(files) > MAX_IMAGES:
        raise HTTPException(status_code=400, detail=f"模板最多 {MAX_IMAGES} 张(当前 {cur} 张)")
    job_id = storage.new_job_id()  # 本次追加的图放一个新目录
    next_idx = max((im.idx for im in tpl.images), default=-1) + 1
    for i, f in enumerate(files):
        raw = await f.read()
        try:
            Image.open(io.BytesIO(raw)).load()
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            raise HTTPException(status_code=400, detail=f"第 {i + 1} 张图无法读取: {exc}") from exc
        name_i = f"product_{i}.png"
        Image.open(io.BytesIO(raw)).convert("RGB").save(storage.output_path(job_id, name_i), format="PNG")
        db.add(MockupTemplateImage(template_id=tpl.id, path=storage.output_url(job_id, name_i), idx=next_idx + i))
    db.commit(); db.refresh(tpl)
    storage.mirror_job(job_id)  # 镜像追加的产品照进对象存储(local no-op)
    return _serialize(tpl)


@router.delete("/mockup-templates/{tid}/images/{img_id}")
def delete_image(tid: int, img_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """删除模板里的某张图(连带删盘)。模板至少保留 1 张(要清空请删整个模板)。"""
    tpl = _own_template(db, tid, user)
    im = db.get(MockupTemplateImage, img_id)
    if im is None or im.template_id != tpl.id:
        raise HTTPException(status_code=404, detail="图片不存在")
    if len(tpl.images) <= 1:
        raise HTTPException(status_code=400, detail="模板至少保留 1 张图;要清空请删除整个模板")
    p = storage.path_from_url(im.path)
    try:
        if p and p.is_file():
            p.unlink()
    except Exception:  # noqa: BLE001
        pass
    storage.delete_object_for_path(p)  # 同步删对象存储副本(local no-op)
    db.delete(im); db.commit(); db.refresh(tpl)
    return _serialize(tpl)


@router.delete("/mockup-templates/{tid}")
def delete_template(tid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """删除套图模板(连带其产品照文件)。同组织可删(团队资源)。"""
    tpl = _own_template(db, tid, user)
    for im in tpl.images:  # 顺手删盘
        p = storage.path_from_url(im.path)
        try:
            if p and p.is_file():
                p.unlink()
        except Exception:  # noqa: BLE001
            pass
        storage.delete_object_for_path(p)  # 同步删对象存储副本(local no-op)
    db.delete(tpl); db.commit()
    return {"id": tid, "deleted": True}
