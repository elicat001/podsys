"""模板管理(batch10):刊登模板 + 导出模板,均 owner 隔离。"""
from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session
from sqlalchemy import select
from ..db import get_db
from ..models_db import User
from ..models_template import ListingTemplate, ExportTemplate
from ..auth import current_user

router = APIRouter(prefix="/api/templates", tags=["templates"])


# --- 刊登模板 ------------------------------------------------------------
class ListingTemplateIn(BaseModel):
    name: str
    platform: str = ""
    fields: dict = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def _name_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("name 不能为空")
        return v


def _listing_dict(t: ListingTemplate) -> dict:
    return {
        "id": t.id,
        "name": t.name,
        "platform": t.platform,
        "fields": t.fields or {},
        "created_at": t.created_at.isoformat() if t.created_at else None,
    }


@router.post("/listing")
def create_listing_template(body: ListingTemplateIn,
                            user: User = Depends(current_user), db: Session = Depends(get_db)):
    t = ListingTemplate(owner_id=user.id, name=body.name.strip(),
                        platform=body.platform, fields=body.fields or {})
    db.add(t); db.commit(); db.refresh(t)
    return _listing_dict(t)


@router.get("/listing")
def list_listing_templates(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.execute(
        select(ListingTemplate).where(ListingTemplate.owner_id == user.id)
        .order_by(ListingTemplate.id.desc())
    ).scalars().all()
    return [_listing_dict(t) for t in rows]


@router.delete("/listing/{tid}")
def delete_listing_template(tid: int,
                            user: User = Depends(current_user), db: Session = Depends(get_db)):
    t = db.get(ListingTemplate, tid)
    if not t or t.owner_id != user.id:
        raise HTTPException(status_code=404, detail="刊登模板不存在")
    db.delete(t); db.commit()
    return {"deleted": tid}


# --- 导出模板 ------------------------------------------------------------
class ExportTemplateIn(BaseModel):
    name: str
    dpi: int = 300
    width_cm: float = 30.0
    height_cm: float = 40.0
    fmt: str = "png"

    @field_validator("name")
    @classmethod
    def _name_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("name 不能为空")
        return v


def _export_dict(t: ExportTemplate) -> dict:
    return {
        "id": t.id,
        "name": t.name,
        "dpi": t.dpi,
        "width_cm": t.width_cm,
        "height_cm": t.height_cm,
        "fmt": t.fmt,
        "created_at": t.created_at.isoformat() if t.created_at else None,
    }


@router.post("/export")
def create_export_template(body: ExportTemplateIn,
                           user: User = Depends(current_user), db: Session = Depends(get_db)):
    t = ExportTemplate(owner_id=user.id, name=body.name.strip(), dpi=body.dpi,
                       width_cm=body.width_cm, height_cm=body.height_cm, fmt=body.fmt)
    db.add(t); db.commit(); db.refresh(t)
    return _export_dict(t)


@router.get("/export")
def list_export_templates(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.execute(
        select(ExportTemplate).where(ExportTemplate.owner_id == user.id)
        .order_by(ExportTemplate.id.desc())
    ).scalars().all()
    return [_export_dict(t) for t in rows]


@router.delete("/export/{tid}")
def delete_export_template(tid: int,
                           user: User = Depends(current_user), db: Session = Depends(get_db)):
    t = db.get(ExportTemplate, tid)
    if not t or t.owner_id != user.id:
        raise HTTPException(status_code=404, detail="导出模板不存在")
    db.delete(t); db.commit()
    return {"deleted": tid}
