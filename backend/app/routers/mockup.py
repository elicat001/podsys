"""商品套图路由。前缀 /api/mockup。

- /render 单张套图(印花 → 指定产品+配色),`charge_for("asset")`(1 点)。
- /batch  批量套图(多模板 × 多配色,一次出一整组),**按张扣点**(asset × N,失败全退)。

引擎:有 key 走 gpt-image 真实感产品图(返回 engine=ai),AI 失败/无 key 自动回退本地
Pillow 合成(engine=local)——离线可跑、不会因 AI 抖动而失败。读图/参数失败 → 400(退点)。
产物入库(我的空间/搜图可见)。
"""
from __future__ import annotations

import io
from itertools import product

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


@router.post("/batch")
async def batch(
    file: UploadFile = File(...),
    templates: str = Form("tshirt"),
    colors: str = Form(""),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """批量套图:templates × colors 笛卡尔积,一次出一整组。按张扣点(asset)。

    colors 留空 = 各模板默认配色;否则对每个模板套用每个配色(非法配色跳过)。
    """
    tpls = [t.strip() for t in templates.split(",") if t.strip()]
    cols = [c.strip() for c in colors.split(",") if c.strip()]
    if not tpls:
        raise HTTPException(status_code=400, detail="未指定产品模板")
    for t in tpls:
        if t not in mockup.BUILTIN:
            raise HTTPException(status_code=400, detail=f"未知产品模板:{t}")
    for c in cols:
        if not _valid_color(c):
            raise HTTPException(status_code=400, detail=f"未知配色:{c}")

    # 组合:有配色→笛卡尔积;无配色→各模板默认色
    if cols:
        combos: list[tuple[str, str | None]] = list(product(tpls, cols))
    else:
        combos = [(t, None) for t in tpls]
    if not (1 <= len(combos) <= MAX_BATCH):
        raise HTTPException(status_code=400, detail=f"套图数量需在 1~{MAX_BATCH} 之间")
    n = len(combos)

    # 按张预扣 n 次(变体范式:任意环节失败把已扣的全退回,保证笔数对齐)
    charged = 0
    try:
        for _ in range(n):
            charge(db, user, "asset")
            charged += 1
    except InsufficientCredits as exc:
        for _ in range(charged):
            refund(db, user, "asset")
        raise HTTPException(status_code=402, detail=str(exc)) from exc

    def _refund_all() -> None:
        for _ in range(n):
            refund(db, user, "asset")

    raw = await file.read()
    try:
        Image.open(io.BytesIO(raw)).load()
    except Exception as exc:  # noqa: BLE001
        _refund_all()
        raise HTTPException(status_code=400, detail=f"无法读取图片: {exc}") from exc

    # 组合存进 params(color 可能为 None → JSON null),worker 重建 combos 渲染(见 tasks._work_mockup_batch)
    return submit_celery(run_tool, db, user, kind="mockup-batch", tool_id="mockupbatch", op="asset",
                         raw=raw, params={"combos": [[t, c] for (t, c) in combos], "n": n}, n=n)


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
