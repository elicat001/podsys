"""我的空间深度 —— 存储配额 + 资产筛选 + 回收站。

前缀 /api/space,均需 current_user + owner 隔离。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..models_db import Asset, User
from ..auth import current_user
from ..services.quota import usage

router = APIRouter(prefix="/api/space", tags=["space"])


def _delete_asset_file(asset: Asset) -> None:
    """删除素材对应的磁盘文件以真正释放空间。asset.path 形如 /files/{job_id}/{name}。"""
    p = asset.path or ""
    if not p.startswith("/files/"):
        return
    rel = p[len("/files/"):]  # {job_id}/{name}
    try:
        fp = settings.outputs_dir / rel
        if fp.is_file():
            fp.unlink()
    except Exception:  # noqa: BLE001 — 删盘失败不应阻断 DB 清理
        pass


def _serialize(a: Asset) -> dict:
    return {
        "id": a.id,
        "name": a.name,
        "source": a.source,
        "risk": a.risk,
        "batch": a.batch,
        "tags": a.tags or [],
        "size_bytes": a.size_bytes,
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


@router.get("/assets")
def list_assets(
    source: str | None = Query(default=None),
    batch: str | None = Query(default=None),
    tagged: bool | None = Query(default=None),
    risk: str | None = Query(default=None),
    q: str | None = Query(default=None),
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
    if tagged is not None:
        # 有标签 = tags 非空列表。SQLite JSON 比较退化,改用 python 侧过滤(见下)。
        pass

    base = select(Asset).where(*conds)
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
