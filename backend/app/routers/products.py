"""商品库 + 一键上架。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import current_user
from ..db import get_db
from ..models_db import Listing, Product, User
from ..services.publish import build_listing_payload, get_publisher

router = APIRouter(prefix="/api/products", tags=["products"])


class ProductIn(BaseModel):
    title: str
    category: str = "apparel"
    price: float = 19.99
    print_path: str = ""
    mockup_path: str = ""
    attrs: dict = {}


@router.post("")
def create_product(body: ProductIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = Product(owner_id=user.id, **body.model_dump())
    db.add(p); db.commit(); db.refresh(p)
    return {"product_id": p.id, "title": p.title}


@router.get("")
def list_products(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.execute(select(Product).where(Product.owner_id == user.id)).scalars().all()
    return [{"id": p.id, "title": p.title, "price": p.price, "category": p.category} for p in rows]


class PublishIn(BaseModel):
    platform: str = "local"


@router.post("/{product_id}/publish")
def publish_product(product_id: int, body: PublishIn,
                    user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = db.get(Product, product_id)
    if not p or p.owner_id != user.id:
        raise HTTPException(status_code=404, detail="商品不存在")
    payload = build_listing_payload(p, body.platform)
    listing = Listing(product_id=p.id, platform=body.platform, payload=payload)
    try:
        result = get_publisher(body.platform).publish(payload)
        listing.status = result["status"]
        listing.external_id = result.get("external_id", "")
    except NotImplementedError as exc:
        listing.status = "draft"
        db.add(listing); db.commit(); db.refresh(listing)
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    db.add(listing); db.commit(); db.refresh(listing)
    return {"listing_id": listing.id, "status": listing.status,
            "external_id": listing.external_id, "payload": payload}
