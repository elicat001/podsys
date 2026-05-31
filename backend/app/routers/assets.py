"""素材库 + 侵权检测。"""
from __future__ import annotations
import io
from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import select
from PIL import Image
from ..db import get_db
from ..models_db import Asset, User
from ..auth import current_user
from .. import storage
from ..services import phash
from ..services.infringement import check_image

router = APIRouter(prefix="/api/assets", tags=["assets"])


def _read_image(raw: bytes) -> Image.Image:
    try:
        im = Image.open(io.BytesIO(raw)); im.load(); return im
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"无法读取图片: {exc}") from exc


@router.post("")
async def add_asset(file: UploadFile = File(...), source: str = Form("upload"),
                    user: User = Depends(current_user), db: Session = Depends(get_db)):
    raw = await file.read()
    img = _read_image(raw)
    # 入库前先做侵权/查重
    chk = check_image(db, img, owner_id=user.id)
    job_id = storage.new_job_id()
    path = storage.output_path(job_id, "asset.png")
    img.convert("RGBA").save(path, format="PNG")
    asset = Asset(owner_id=user.id, name=file.filename or "asset", path=str(path),
                  dhash=chk["dhash"], chash=chk["chash"], source=source, risk=chk["risk"],
                  size_bytes=len(raw))
    db.add(asset); db.commit(); db.refresh(asset)
    return {"asset_id": asset.id, "risk": asset.risk, "url": storage.output_url(job_id, "asset.png"),
            "infringement": chk}


@router.get("")
def list_assets(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.execute(select(Asset).where(Asset.owner_id == user.id)).scalars().all()
    return [{"id": a.id, "name": a.name, "risk": a.risk, "source": a.source, "dhash": a.dhash}
            for a in rows]


@router.post("/check")
async def infringement_check(file: UploadFile = File(...),
                             user: User = Depends(current_user), db: Session = Depends(get_db)):
    """只检测不入库:返回风险评级 + 相似命中。"""
    img = _read_image(await file.read())
    return check_image(db, img, owner_id=user.id)
