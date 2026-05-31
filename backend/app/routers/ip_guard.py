"""侵权检测升级路由:TRO + 艺术家版权库 + 深度检索报告。

前缀 /api/ip-guard:
  - POST /scan    上传图 + 可选 title,深度侵权检索(扣 process=2 点,读图失败退点)。
  - GET  /library 查看本地种子库统计(条数 / 类型分布)。
"""
from __future__ import annotations

import io

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from PIL import Image
from sqlalchemy.orm import Session

from ..auth import current_user
from ..db import get_db
from ..models_db import User
from ..services import ip_guard
from ..services.billing import charge_for, refund
from .. import storage

router = APIRouter(prefix="/api/ip-guard", tags=["ip-guard"])


@router.post("/scan")
async def scan(
    file: UploadFile = File(...),
    title: str = Form(""),
    user: User = Depends(charge_for("process")),
    db: Session = Depends(get_db),
):
    """深度侵权检索:返回 scan() 报告 + job_id。"""
    raw = await file.read()
    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except Exception as exc:  # noqa: BLE001
        refund(db, user, "process")
        raise HTTPException(status_code=400, detail=f"无法读取图片: {exc}") from exc

    report = ip_guard.scan(img, title=title or None)
    report["job_id"] = storage.new_job_id()
    return report


@router.get("/library")
def library(user: User = Depends(current_user)):
    """本地侵权风险种子库统计。"""
    return ip_guard.library_stats()
