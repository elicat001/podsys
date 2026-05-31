"""以图搜图路由 /api/search:在自己的素材库里按相似度检索(免费,不扣点)。"""
from __future__ import annotations

import io

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from PIL import Image
from sqlalchemy.orm import Session

from ..auth import current_user
from ..db import get_db
from ..models_db import User
from ..services.search import search_assets

router = APIRouter(prefix="/api/search", tags=["search"])


@router.post("/by-image")
async def by_image(
    file: UploadFile = File(...),
    top_k: int = Form(10),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """上传一张图,返回该用户素材库中最相似的 top_k 条。搜自己库免费,不扣点。"""
    raw = await file.read()
    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"无法读取图片: {exc}") from exc

    results = search_assets(db, owner_id=user.id, image=img, top_k=top_k)
    return {"count": len(results), "results": results}
