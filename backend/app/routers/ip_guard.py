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
from ..tasks import run_tool
from ..web_utils import read_image_or_refund, submit_celery

router = APIRouter(prefix="/api/ip-guard", tags=["ip-guard"])


@router.post("/scan")
def scan(
    file: UploadFile = File(...),
    title: str = Form(""),
    verbose: bool = Form(False),
    user: User = Depends(charge_for("process")),
    db: Session = Depends(get_db),
):
    """深度侵权检索 → Celery 后台作业(提交即走、任务中心看报告)。

    P2-2:默认只回 risk/advice/命中条数;`verbose=true` 才回命中明细(worker 内按 verbose 裁剪)。
    """
    raw = file.file.read()
    read_image_or_refund(raw, db, user, "process")  # 读图失败 → 400 + 退点
    return submit_celery(run_tool, db, user, kind="ipguard", tool_id="ipguard", op="process",
                         raw=raw, params={"title": title, "verbose": verbose})


@router.get("/library")
def library(user: User = Depends(current_user)):
    """本地侵权风险种子库统计。"""
    return ip_guard.library_stats()
