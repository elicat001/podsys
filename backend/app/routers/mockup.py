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
from ..db import get_db
from ..models_db import User
from ..services import mockup
from ..services.billing import InsufficientCredits, charge, charge_for, refund
from ..services.library import save_as_asset
from ..web_utils import read_image_or_refund

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
    """单张套图。color 留空=该产品默认配色。"""
    src = read_image_or_refund(await file.read(), db, user, "asset")
    if template not in mockup.BUILTIN:
        refund(db, user, "asset")
        raise HTTPException(status_code=400, detail="未知产品模板")
    if color and not _valid_color(color):
        refund(db, user, "asset")
        raise HTTPException(status_code=400, detail="未知配色")

    img, engine = mockup.render_product(src, template, color or None)
    job_id = storage.new_job_id()
    name = f"mockup_{template}_{color or 'default'}.png"
    img.save(storage.output_path(job_id, name), format="PNG")
    url = storage.output_url(job_id, name)
    save_as_asset(db, user.id, img, f"套图 {template}", url, source="generated")
    return {"job_id": job_id, "image_url": url, "template": template,
            "color": color or None, "engine": engine}


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

    try:
        src = Image.open(io.BytesIO(await file.read())); src.load()
    except Exception as exc:  # noqa: BLE001
        _refund_all()
        raise HTTPException(status_code=400, detail=f"无法读取图片: {exc}") from exc

    try:
        rendered = mockup.render_variants(src, combos)
    except Exception as exc:  # noqa: BLE001
        _refund_all()
        raise HTTPException(status_code=502, detail="批量套图失败,请稍后重试") from exc

    job_id = storage.new_job_id()
    items = []
    for tid, color, im in rendered:
        name = f"mockup_{tid}_{color}.png"
        im.save(storage.output_path(job_id, name), format="PNG")
        url = storage.output_url(job_id, name)
        save_as_asset(db, user.id, im, f"套图 {tid}/{color}", url, source="generated")
        items.append({"template": tid, "color": color, "url": url})
    return {"job_id": job_id, "items": items, "count": len(items)}
