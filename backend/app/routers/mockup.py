"""商品套图路由。前缀 /api/mockup。

- /render  单张套图(印花 → 指定产品+配色),`charge_for("asset")`(1 点)。
- /replace 套图模板印花替换(把模板/上传产品照里的原印花换成新印花),按张扣点(asset × N)。

引擎:有 key 走 gpt-image 真实感产品图(返回 engine=ai),AI 失败/无 key 自动回退本地
Pillow 合成(engine=local)——离线可跑、不会因 AI 抖动而失败。读图/参数失败 → 400(退点)。
产物入库(我的空间可见)。
"""
from __future__ import annotations

import io

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from PIL import Image
from sqlalchemy.orm import Session

from .. import storage
from ..auth import current_user
from ..config import settings
from ..db import get_db
from ..models_db import User
from ..models_team import MockupTemplate
from ..services import mockup
from ..services.billing import InsufficientCredits, charge, charge_for, refund
from ..services.jobs import create_job
from ..services.quota import usage
from ..tasks import run_tool
from ..web_utils import enqueue_or_refund, read_image_or_refund, submit_celery

router = APIRouter(prefix="/api/mockup", tags=["mockup"])

MAX_BATCH = 12


def _valid_color(c: str) -> bool:
    return c in mockup.GARMENT_COLORS


@router.post("/render")
async def render(
    file: UploadFile = File(...),
    template: str = Form("tshirt"),
    color: str = Form(""),
    user: User = Depends(charge_for("asset")),
    db: Session = Depends(get_db),
):
    """单张套图。color 留空=该产品默认配色。→ Celery 后台作业,前端轮询。"""
    raw = await file.read()
    read_image_or_refund(raw, db, user, "asset")  # 读图失败 → 400 + 退点
    if template not in mockup.BUILTIN:
        refund(db, user, "asset")
        raise HTTPException(status_code=400, detail="未知产品模板")
    if color and not _valid_color(color):
        refund(db, user, "asset")
        raise HTTPException(status_code=400, detail="未知配色")

    return submit_celery(run_tool, db, user, kind="mockup", tool_id="mockup", op="asset",
                         raw=raw, params={"template": template, "color": color or None})


@router.post("/replace")
async def replace(
    file: UploadFile = File(...),                          # 新印花
    template_id: int = Form(0),                            # 团队资源套图模板(0=用上传的产品照)
    mockups: list[UploadFile] | None = File(None),         # 临时上传的产品照(template_id=0 时用)
    engine: str = Form("auto"),                            # ai=智能(gpt 真实印制)| fast=快速(本地几何)| auto=有 key 走 AI
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """套图模板印花替换:把模板/上传的每张产品照里的原印花换成上传的新印花。按张扣点(asset×N)。

    来源二选一:template_id>0 → 用团队资源套图模板的全部图;否则用 mockups 上传的产品照。
    异步 Celery 作业;worker 逐张 detect+replace(见 tasks._work_mockup_replace)。
    """
    print_raw = await file.read()
    try:
        Image.open(io.BytesIO(print_raw)).load()
    except Exception as exc:  # noqa: BLE001 — 还没扣点,直接 400
        raise HTTPException(status_code=400, detail=f"印花图无法读取: {exc}") from exc

    # 确定产品图来源与数量 N
    mockup_raws: list[bytes] = []
    if template_id:
        tpl = db.get(MockupTemplate, template_id)
        if tpl is None or tpl.org_id != user.org_id:
            raise HTTPException(status_code=404, detail="套图模板不存在")
        n = len(tpl.images)
    else:
        for i, m in enumerate(mockups or []):
            raw = await m.read()
            try:
                Image.open(io.BytesIO(raw)).load()
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(status_code=400, detail=f"第 {i + 1} 张产品图无法读取: {exc}") from exc
            mockup_raws.append(raw)
        n = len(mockup_raws)
    if not (1 <= n <= MAX_BATCH):
        raise HTTPException(status_code=400, detail=f"套图数量需在 1~{MAX_BATCH} 之间")
    if engine == "ai" and not settings.openai_api_key:  # 智能需 key(还没扣点,直接拒)
        raise HTTPException(status_code=502, detail="智能运行需配置 AI key;可改用「快速运行」(本地)")

    if usage(db, user.id)["over"]:  # 超容:还没扣点,直接 413
        raise HTTPException(status_code=413, detail="存储空间已用满(上限 2GB),请到「我的空间」清理后重试")

    # 按张预扣 N 次(任意环节失败全退,笔数对齐)
    charged = 0
    try:
        for _ in range(n):
            charge(db, user, "asset"); charged += 1
    except InsufficientCredits as exc:
        for _ in range(charged):
            refund(db, user, "asset")
        raise HTTPException(status_code=402, detail=str(exc)) from exc

    job = create_job(db, "mockup-replace", owner_id=user.id, tool_id="mockup",
                     params={"template_id": template_id, "n": n, "engine": engine})
    storage.upload_path(job.id).write_bytes(print_raw)               # 新印花
    for i, mraw in enumerate(mockup_raws):                           # 临时产品照(若有)
        storage.upload_path(f"{job.id}_m{i}").write_bytes(mraw)
    enqueue_or_refund(run_tool, job, db, user, "asset", n=n)         # broker 挂 → 退 N + 502
    return {"job_id": job.id, "status": "pending"}
