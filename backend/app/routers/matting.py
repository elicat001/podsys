"""一键抠图路由。前缀 /api/matting。

纯去背景 → 透明 PNG(优先 rembg/u2net 智能抠图,边缘干净;缺包/失败兜底 pillow),
**不套图、不导出三件套**。耗时丢 Celery 后台(前端提交即走、任务中心看结果)。
计费:charge_for("process")(2 点);读图失败 → 400 + 退点。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..models_db import User
from ..services.billing import charge_for, refund
from ..tasks import run_tool
from ..web_utils import read_image_or_refund, submit_celery

router = APIRouter(prefix="/api/matting", tags=["matting"])


@router.post("")
async def matting(
    file: UploadFile = File(...),
    engine: str = Form("fast"),   # fast=快速(本地保真去背景)| ai=智能(gpt 识别主体并扣出,需 key)
    prompt: str = Form(""),       # 智能运行的可选主体提示(消歧/难图补充线索;快速运行忽略)
    user: User = Depends(charge_for("process")),
    db: Session = Depends(get_db),
):
    """一键抠图 → Celery 后台作业,返回 {job_id, status:"pending"}(结果含透明 PNG image_url)。

    双引擎:fast(默认,本地;旧的「运行」即此)/ ai(智能,gpt 识别主体连手/道具一起扣)。
    本地抠图保真、免依赖网关,故默认仍走 fast;仅显式选「智能运行」才走 AI(无 key → 退点 + 502)。
    """
    raw = await file.read()
    read_image_or_refund(raw, db, user, "process")  # 读图失败 → 400 + 退点

    if engine == "ai" and not settings.openai_api_key:
        refund(db, user, "process")
        raise HTTPException(status_code=502, detail="智能运行需配置 AI key;可改用「快速运行」(本地)")
    eng = "ai" if engine == "ai" else "fast"   # 只有显式 ai 才走 AI,其余(fast/auto/空)= 本地保真
    return submit_celery(run_tool, db, user, kind="matting", tool_id="matting",
                         op="process", raw=raw, params={"engine": eng, "prompt": prompt})
