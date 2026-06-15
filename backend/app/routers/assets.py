"""素材库 + 侵权检测。"""
from __future__ import annotations

import io

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import storage
from ..auth import current_user
from ..db import get_db
from ..models_db import Asset, User
from ..services.infringement import check_image

router = APIRouter(prefix="/api/assets", tags=["assets"])


def _read_image(raw: bytes) -> Image.Image:
    try:
        im = Image.open(io.BytesIO(raw)); im.load(); return im
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"无法读取图片: {exc}") from exc


@router.post("")
def add_asset(file: UploadFile = File(...), source: str = Form("upload"),
              user: User = Depends(current_user), db: Session = Depends(get_db)):
    raw = file.file.read()
    img = _read_image(raw)
    # 入库前先做侵权/查重
    chk = check_image(db, img, owner_id=user.id)
    job_id = storage.new_job_id()
    img.convert("RGBA").save(storage.output_path(job_id, "asset.png"), format="PNG")
    # path 存 /files/ URL(与 collect_tasks.sync_images / save_as_asset 一致):
    # 这样「永久删除」(space._delete_asset_file)能真删盘释放空间,quota 磁盘游走也能正确计入。
    # 历史 bug:这里曾存磁盘绝对路径,purge 只认 /files/ 前缀→跳过不删→DB 行删了文件却残留(存储泄漏)。
    url = storage.output_url(job_id, "asset.png")
    asset = Asset(owner_id=user.id, name=file.filename or "asset", path=url,
                  dhash=chk["dhash"], chash=chk["chash"], source=source, risk=chk["risk"],
                  size_bytes=len(raw))
    db.add(asset); db.commit(); db.refresh(asset)
    storage.mirror_job(job_id)  # 镜像进对象存储(local no-op)
    return {"asset_id": asset.id, "risk": asset.risk, "url": url, "infringement": chk}


@router.get("")
def list_assets(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.execute(select(Asset).where(Asset.owner_id == user.id)).scalars().all()
    return [{"id": a.id, "name": a.name, "risk": a.risk, "source": a.source, "dhash": a.dhash}
            for a in rows]


@router.post("/check")
def infringement_check(file: UploadFile = File(...),
                       user: User = Depends(current_user), db: Session = Depends(get_db)):
    """只检测不入库:返回风险评级 + 相似命中。"""
    img = _read_image(file.file.read())
    return check_image(db, img, owner_id=user.id)
