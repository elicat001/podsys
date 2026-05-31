"""侵权检测升级路由:TRO + 艺术家版权库 + 深度检索报告。

前缀 /api/ip-guard:
  - POST /scan    上传图 + 可选 title,深度侵权检索(扣 process=2 点,读图失败退点)。
  - GET  /library 查看本地种子库统计(条数 / 类型分布)。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy.orm import Session

from ..auth import current_user
from ..db import get_db
from ..models_db import User
from ..services import ip_guard
from ..services.billing import charge_for
from ..web_utils import read_image_or_refund
from .. import storage

router = APIRouter(prefix="/api/ip-guard", tags=["ip-guard"])


@router.post("/scan")
async def scan(
    file: UploadFile = File(...),
    title: str = Form(""),
    verbose: bool = Form(False),
    user: User = Depends(charge_for("process")),
    db: Session = Depends(get_db),
):
    """深度侵权检索:返回 scan() 报告 + job_id。

    P2-2:默认只回 risk/advice/命中条数;`verbose=true` 才回命中条目明细
    (品牌/距离/命中关键词),避免向终端用户泄露侵权库结构与判定阈值。
    """
    img = read_image_or_refund(await file.read(), db, user, "process")
    report = ip_guard.scan(img, title=title or None)
    if not verbose:
        report = {
            "risk": report.get("risk"),
            "advice": report.get("advice"),
            "match_count": len(report.get("matches", [])),
            "checked": report.get("checked"),
        }
    report["job_id"] = storage.new_job_id()
    return report


@router.get("/library")
def library(user: User = Depends(current_user)):
    """本地侵权风险种子库统计。"""
    return ip_guard.library_stats()
