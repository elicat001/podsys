"""店铺管理 + 按店铺上架。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import current_user
from ..db import get_db
from ..models_db import Listing, Product, User
from ..models_shop import Shop
from ..services.publish import build_listing_payload, get_publisher

router = APIRouter(prefix="/api/shops", tags=["shops"])


class ShopIn(BaseModel):
    platform: str
    name: str


@router.post("")
def create_shop(body: ShopIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    shop = Shop(owner_id=user.id, platform=body.platform, name=body.name)
    db.add(shop); db.commit(); db.refresh(shop)
    return {"shop_id": shop.id, "platform": shop.platform, "name": shop.name}


@router.get("")
def list_shops(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.execute(select(Shop).where(Shop.owner_id == user.id)).scalars().all()
    return [
        {"shop_id": s.id, "platform": s.platform, "name": s.name, "status": s.status}
        for s in rows
    ]


class PublishProductIn(BaseModel):
    product_id: int


@router.post("/{shop_id}/publish-product")
def publish_product_to_shop(shop_id: int, body: PublishProductIn,
                            user: User = Depends(current_user), db: Session = Depends(get_db)):
    shop = db.get(Shop, shop_id)
    if not shop or shop.owner_id != user.id:
        raise HTTPException(status_code=404, detail="店铺不存在")
    product = db.get(Product, body.product_id)
    if not product or product.owner_id != user.id:
        raise HTTPException(status_code=404, detail="商品不存在")

    payload = build_listing_payload(product, shop.platform)
    payload["shop_id"] = shop_id

    listing = Listing(product_id=product.id, platform=shop.platform, payload=payload)
    try:
        result = get_publisher(shop.platform).publish(payload)
        listing.status = result["status"]
        listing.external_id = result.get("external_id", "")
    except NotImplementedError as exc:
        listing.status = "draft"
        db.add(listing); db.commit(); db.refresh(listing)
        raise HTTPException(status_code=501, detail=str(exc)) from exc

    db.add(listing); db.commit(); db.refresh(listing)
    return {"listing_id": listing.id, "status": listing.status,
            "external_id": listing.external_id, "shop_id": shop_id}
