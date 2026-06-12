"""商品库管理深度(batch10 E1):多维筛选 + 批量操作 + 标签。

独立 router,前缀复用 /api/products(路径与 routers/products.py 错开:
products.py 用 POST ""/GET ""/POST /{id}/publish;本文件用 /search、/batch、/{id}/tags)。
均 current_user + owner 隔离。
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import current_user
from ..db import get_db
from ..models_db import Listing, Product, User

router = APIRouter(prefix="/api/products", tags=["product-admin"])


def _listing_status(db: Session, product_id: int) -> str:
    """published(有 published listing)/draft(有 listing 但无 published)/none(无 listing)。"""
    statuses = db.execute(
        select(Listing.status).where(Listing.product_id == product_id)
    ).scalars().all()
    if not statuses:
        return "none"
    if any(s == "published" for s in statuses):
        return "published"
    return "draft"


def _serialize(db: Session, p: Product) -> dict:
    return {
        "id": p.id,
        "title": p.title,
        "sku": p.sku,
        "batch": p.batch,
        "source": p.source,
        "risk": p.risk,
        "tags": list(p.tags or []),
        "listing_status": _listing_status(db, p.id),
    }


@router.get("/search")
def search_products(
    source: Optional[str] = Query(default=None),
    risk: Optional[str] = Query(default=None),
    batch: Optional[str] = Query(default=None),
    sku: Optional[str] = Query(default=None),
    tag: Optional[str] = Query(default=None),
    listing_status: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    """多维筛选商品(owner 隔离)。tag 用 JSON 包含;listing_status 在 Python 侧判定。"""
    stmt = select(Product).where(Product.owner_id == user.id)
    if source is not None:
        stmt = stmt.where(Product.source == source)
    if risk is not None:
        stmt = stmt.where(Product.risk == risk)
    if batch is not None:
        stmt = stmt.where(Product.batch == batch)
    if sku is not None:
        stmt = stmt.where(Product.sku == sku)
    stmt = stmt.order_by(Product.id)

    rows = db.execute(stmt).scalars().all()

    # tag:JSON list 包含(Python 侧过滤,简单可移植;量大可改 MySQL JSON_CONTAINS)
    if tag is not None:
        rows = [p for p in rows if tag in (p.tags or [])]

    # listing_status:按每个商品的 Listing 状态过滤
    if listing_status is not None:
        rows = [p for p in rows if _listing_status(db, p.id) == listing_status]

    total = len(rows)
    page = rows[offset : offset + limit]
    items = [_serialize(db, p) for p in page]
    return {"total": total, "items": items}


_VALID_ACTIONS = {"delete", "set_risk", "add_tag"}


class BatchIn(BaseModel):
    action: str
    product_ids: list[int] = Field(..., max_length=500)
    value: Optional[str] = None


@router.post("/batch")
def batch_op(
    body: BatchIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    """批量操作,仅对本人商品生效。action∈{delete,set_risk,add_tag};返回 affected。"""
    if body.action not in _VALID_ACTIONS:
        raise HTTPException(status_code=400, detail="非法 action")
    if not body.product_ids:
        return {"affected": 0}

    rows = db.execute(
        select(Product).where(
            Product.owner_id == user.id,
            Product.id.in_(body.product_ids),
        )
    ).scalars().all()

    affected = 0
    if body.action == "delete":
        for p in rows:
            db.delete(p)
            affected += 1
    elif body.action == "set_risk":
        new_risk = body.value or ""
        if new_risk not in ("safe", "review", "high", "unknown"):  # 评审 P2-1:风险值白名单
            raise HTTPException(status_code=400, detail="risk 取值须为 safe|review|high|unknown")
        for p in rows:
            p.risk = new_risk
            affected += 1
    elif body.action == "add_tag":
        new_tag = body.value
        if new_tag:
            for p in rows:
                tags = list(p.tags or [])
                if new_tag not in tags:
                    tags.append(new_tag)
                p.tags = tags
                affected += 1
        else:
            affected = len(rows)

    db.commit()
    return {"affected": affected}


class TagsIn(BaseModel):
    tags: list[str]


@router.post("/{product_id}/tags")
def set_tags(
    product_id: int,
    body: TagsIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    """覆盖该商品 tags(非本人/不存在 404)。"""
    p = db.get(Product, product_id)
    if not p or p.owner_id != user.id:
        raise HTTPException(status_code=404, detail="商品不存在")
    p.tags = list(body.tags)
    db.commit()
    db.refresh(p)
    return {"id": p.id, "tags": list(p.tags or [])}
