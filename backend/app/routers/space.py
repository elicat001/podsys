"""我的空间深度 —— 存储配额 + 资产筛选 + 回收站。

前缀 /api/space,均需 current_user + owner 隔离。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import storage
from ..auth import current_user
from ..config import settings
from ..db import get_db
from ..models_db import Asset, User
from ..services import collect_tasks as collect_svc
from ..services.quota import usage

router = APIRouter(prefix="/api/space", tags=["space"])


def _delete_asset_file(asset: Asset) -> None:
    """删除素材对应的磁盘文件以真正释放空间。
    兼容两种 path:① /files/{job_id}/{name}(现行);② outputs 下的磁盘绝对路径(历史遗留,
    ecd3ac3 之前 /api/assets 这么存)。两者都能定位文件 + 连带删缩略图缓存。"""
    p = asset.path or ""
    fp: Path | None = None
    if p.startswith("/files/"):
        fp = settings.outputs_dir / p[len("/files/"):]
    else:
        try:
            cand = Path(p)
            # 只删 outputs 目录下的文件,绝不越界删别处
            cand.resolve().relative_to(settings.outputs_dir.resolve())
            fp = cand
        except Exception:  # noqa: BLE001 — 非 outputs 下路径 → 不处理
            fp = None
    if fp is None:
        return
    try:
        if fp.is_file():
            fp.unlink()
        # 连同该图的缩略图缓存一起删(.thumb_<w>_<name>.png),避免孤儿小文件残留
        for th in fp.parent.glob(f".thumb_*_{fp.name}.png"):
            try:
                th.unlink()
            except Exception:  # noqa: BLE001
                pass
    except Exception:  # noqa: BLE001 — 删盘失败不应阻断 DB 清理
        pass
    storage.delete_object_for_path(fp)  # 同步删对象存储里的副本(local no-op;缩略图不入桶不必删)


def _serialize(a: Asset) -> dict:
    return {
        "id": a.id,
        "name": a.name,
        "source": a.source,
        "risk": a.risk,
        "batch": a.batch,
        "tags": a.tags or [],
        "size_bytes": a.size_bytes,
        "url": storage.url_from_path(a.path),   # 对外 /files/ 直链(供管理页缩略图/下载)
        "created_at": a.created_at.isoformat() if a.created_at else None,
    }


def _own_asset(db: Session, asset_id: int, owner_id: int) -> Asset:
    """取本人资产,否则 404(越权不泄露存在性)。"""
    asset = db.get(Asset, asset_id)
    if asset is None or asset.owner_id != owner_id:
        raise HTTPException(status_code=404, detail="资产不存在")
    return asset


@router.get("/quota")
def get_quota(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return usage(db, user.id)


@router.get("/collected")
def list_collected(platform: str | None = Query(default=None),
                   user: User = Depends(current_user), db: Session = Depends(get_db)):
    """找图库:已同步的采集图,按平台分组(Temu/Amazon/…)。"""
    return {"groups": collect_svc.list_collected(db, owner_id=user.id, platform=platform)}


@router.delete("/collected/{image_id}")
def delete_collected(image_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """从找图移除 → 对应素材进回收站(可恢复/可永久删释放空间)。"""
    ok = collect_svc.delete_collected(db, owner_id=user.id, image_id=image_id)
    if not ok:
        raise HTTPException(status_code=404, detail="采集图不存在")
    return {"id": image_id, "deleted": True}


def _parse_day(s: str | None) -> datetime | None:
    """把 'YYYY-MM-DD' 解析成当天 0 点;非法/空 → None(该日期过滤跳过)。"""
    if not s:
        return None
    try:
        return datetime.strptime(s.strip()[:10], "%Y-%m-%d")
    except ValueError:
        return None


@router.get("/assets")
def list_assets(
    source: str | None = Query(default=None),
    batch: str | None = Query(default=None),
    tagged: bool | None = Query(default=None),
    risk: str | None = Query(default=None),
    q: str | None = Query(default=None),
    order: str = Query(default="new"),          # new=最新在前(默认) | old=最早在前
    date_from: str | None = Query(default=None),  # YYYY-MM-DD,含当天起
    date_to: str | None = Query(default=None),    # YYYY-MM-DD,含当天止
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    conds = [Asset.owner_id == user.id, Asset.deleted == False]  # noqa: E712
    if source:
        conds.append(Asset.source == source)
    if batch:
        conds.append(Asset.batch == batch)
    if risk:
        conds.append(Asset.risk == risk)
    if q:
        conds.append(Asset.name.ilike(f"%{q}%"))
    # 按创建时间范围过滤(created_at 按 UTC 存;date_to 含当天 = < 次日 0 点)。走 ix_assets_owner_created 索引。
    df = _parse_day(date_from)
    if df is not None:
        conds.append(Asset.created_at >= df)
    dt_ = _parse_day(date_to)
    if dt_ is not None:
        conds.append(Asset.created_at < dt_ + timedelta(days=1))
    if tagged is not None:
        # 有标签 = tags 非空列表。JSON 列含标签判断用 python 侧过滤(简单可移植,见下)。
        pass

    order_col = Asset.created_at.asc() if order == "old" else Asset.created_at.desc()
    base = select(Asset).where(*conds).order_by(order_col)
    rows = db.execute(base).scalars().all()

    if tagged is not None:
        rows = [a for a in rows if bool(a.tags) == tagged]

    total = len(rows)
    items = rows[offset: offset + limit]
    return {"total": total, "items": [_serialize(a) for a in items]}


@router.get("/trash")
def list_trash(
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    base = select(Asset).where(Asset.owner_id == user.id, Asset.deleted == True)  # noqa: E712
    rows = db.execute(base).scalars().all()
    total = len(rows)
    items = rows[offset: offset + limit]
    return {"total": total, "items": [_serialize(a) for a in items]}


@router.post("/assets/{asset_id}/trash")
def trash_asset(asset_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    asset = _own_asset(db, asset_id, user.id)
    asset.deleted = True
    db.add(asset); db.commit()
    return {"id": asset.id, "deleted": True}


@router.post("/assets/{asset_id}/restore")
def restore_asset(asset_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    asset = _own_asset(db, asset_id, user.id)
    asset.deleted = False
    db.add(asset); db.commit()
    return {"id": asset.id, "deleted": False}


@router.delete("/assets/{asset_id}/purge")
def purge_asset(asset_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    asset = _own_asset(db, asset_id, user.id)
    _delete_asset_file(asset)  # 真删盘释放空间(不只是删 DB 行)
    db.delete(asset); db.commit()
    return {"id": asset_id, "purged": True}


class IdsIn(BaseModel):
    ids: list[int] = Field(default_factory=list, max_length=1000)


@router.post("/assets/trash-batch")
def trash_batch(body: IdsIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """批量移入回收站(管理页多选清理)。只动本人资产。"""
    if not body.ids:
        return {"trashed": 0}
    rows = db.execute(
        select(Asset).where(Asset.owner_id == user.id, Asset.id.in_(body.ids))
    ).scalars().all()
    for a in rows:
        a.deleted = True
    db.commit()
    return {"trashed": len(rows)}


@router.delete("/trash")
def empty_trash(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """清空回收站:逐条删盘 + 删行,真正释放存储。"""
    rows = db.execute(
        select(Asset).where(Asset.owner_id == user.id, Asset.deleted == True)  # noqa: E712
    ).scalars().all()
    n = 0
    for a in rows:
        _delete_asset_file(a)
        db.delete(a)
        n += 1
    db.commit()
    return {"purged": n}
