"""标题提取路由(E3)。前缀 /api/studio。

- /title  标题提取(gpt 文本):智能(有 key 识图 SEO,扣 1;降级则退)/ 快速(本地规则,免费)。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from .. import storage
from ..auth import current_user
from ..db import get_db
from ..models_db import User
from ..services import studio_tools
from ..services.billing import InsufficientCredits, charge, refund
from ..services.jobs import create_job
from ..tasks import run_tool

router = APIRouter(prefix="/api/studio", tags=["studio"])


@router.post("/title")
async def title(
    keywords: str = Form(""),
    category: str = Form("apparel"),
    file: UploadFile | None = File(None),
    engine: str = Form("auto"),   # ai=智能(识图 SEO,需 key,扣1)| fast=快速(本地规则,免费)| auto=有 key 走 AI
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """标题提取 → Celery 后台作业(提交即走、任务中心看)。智能(AI 识图扣 1;降级则退);快速(本地规则免费)。"""
    if engine == "ai" and not studio_tools.has_openai_key():
        raise HTTPException(status_code=502, detail="智能运行需配置 AI key;可改用「快速运行」(本地)")
    eng = "ai" if (engine == "ai" or (engine == "auto" and studio_tools.has_openai_key())) else "fast"

    # 智能(ai)预扣 1 点;快速免费。降级/失败的退点在 worker(_work_title)内处理。
    if eng == "ai":
        try:
            charge(db, user, "title")
        except InsufficientCredits as exc:
            raise HTTPException(status_code=402, detail=str(exc)) from exc

    raw = None  # 可选辅助图
    if file is not None:
        try:
            r = await file.read()
            raw = r or None
        except Exception:  # noqa: BLE001
            raw = None

    job = create_job(db, "title", owner_id=user.id, tool_id="title",
                     params={"keywords": keywords, "category": category, "engine": eng})
    if raw is not None:
        storage.upload_path(job.id).write_bytes(raw)
    try:
        run_tool.delay(job.id)
    except Exception as exc:  # noqa: BLE001 — broker 挂了:智能退点 + 502
        if eng == "ai":
            refund(db, user, "title")
        job.status = "error"; job.error = f"队列不可用: {type(exc).__name__}"; db.commit()
        raise HTTPException(status_code=502, detail="后台队列暂时不可用,请稍后重试(点数已退回)") from exc
    return {"job_id": job.id, "status": "pending"}


