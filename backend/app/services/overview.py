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
    return {
        "credits": user.credits,
        "assets": _count(db, Asset, user.id),
        "products": _count(db, Product, user.id),
        "shops": _count(db, Shop, user.id),
        "jobs": _count(db, Job, user.id),
        "collect_tasks": _count(db, CollectionTask, user.id),
    }
