"""我的空间总览聚合 —— 统计某用户在各表的资源计数。"""
from __future__ import annotations
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from ..models_db import User, Asset, Product, Job
from ..models_shop import Shop
from ..models_collect import CollectionTask


def _count(db: Session, model, owner_id: int) -> int:
    """统计某表中 owner_id == 指定用户 的行数。"""
    return int(
        db.execute(
            select(func.count()).select_from(model).where(model.owner_id == owner_id)
        ).scalar_one()
    )


def overview(db: Session, user: User) -> dict:
    """返回该用户的资源总览:余额 + 各表计数。"""
    # assets 计数排除回收站(deleted),与 space/quota 口径一致(评审 P1-1)
    assets = int(db.execute(
        select(func.count()).select_from(Asset)
        .where(Asset.owner_id == user.id, Asset.deleted == False)  # noqa: E712
    ).scalar_one())
    return {
        "credits": user.credits,
        "assets": assets,
        "products": _count(db, Product, user.id),
        "shops": _count(db, Shop, user.id),
        "jobs": _count(db, Job, user.id),
        "collect_tasks": _count(db, CollectionTask, user.id),
    }
