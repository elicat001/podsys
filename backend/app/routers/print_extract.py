"""印花提取接口。

引擎在 `services/print_extract.py` 决定:默认 AI 重绘(慢 ~90s),无 key/失败降级本地保真(快)。
本路由按速度分两条响应:
- **AI 路径(慢)→ 后台作业**(`submit_ai_job`):立即返回 `{job_id, status:"pending"}`,前端轮询
  `GET /api/jobs/{id}`。避免同步占满 90s 连接(慢网/代理下会 Failed to fetch),并与
  generate/edit 等 AI 工具行为统一;将来换 Celery/RQ 也只动 jobs 实现、接口契约不变。
- **本地路径(快)→ 同步**直接返回结果(无 key 时即此路径,离线测试走这条、保持确定性)。
"""
from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile
from PIL import Image
from sqlalchemy.orm import Session

from .. import storage
from ..config import settings
from ..db import get_db
from ..models_db import User
from ..services.billing import charge_for, refund
from ..services.jobs import save_asset_in_background, submit_ai_job
from ..services.library import save_as_asset
from ..services.print_extract import extract_print_design
from ..web_utils import read_image_or_refund

router = APIRouter(prefix="/api/print-extract", tags=["print-extract"])


def _save_outputs(jid: str, design: Image.Image, meta: dict) -> tuple[str, dict]:
    """存透明版 + 白底版,返回 (透明图 url, 结果 dict)。同步/后台两条路径共用。"""
    url = storage.output_url(jid, "design.png")
    design.save(storage.output_path(jid, "design.png"), format="PNG")
    # 白底版:透明区填白,便于下载/预览(深色看图器里透明会显黑)。透明版仍保留(套版/印刷用)。
    white = Image.new("RGB", design.size, (255, 255, 255))
    white.paste(design, (0, 0), design)
    white.save(storage.output_path(jid, "design_white.png"), format="PNG")
    return url, {"image_url": url, "white_url": storage.output_url(jid, "design_white.png"), **meta}


@router.post("")
def print_extract(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    user: User = Depends(charge_for("process")),
    db: Session = Depends(get_db),
):
    raw = file.file.read()
    src = read_image_or_refund(raw, db, user, "process")  # 读图失败 → 400 + 退点

    # AI 路径(慢)→ 后台作业:立即返回 job_id,前端轮询;失败自动退点(submit_ai_job 内置)
    if settings.print_extract_ai and settings.openai_api_key:
        uid = user.id

        def _work(jid: str) -> dict:
            storage.upload_path(jid).write_bytes(raw)  # 存原图,便于排查真实失败案例
            design, meta = extract_print_design(src)
            url, result = _save_outputs(jid, design, meta)
            save_asset_in_background(uid, design, "印花提取", url)  # 后台用独立 session 入库
            return result

        jid = submit_ai_job(background_tasks, db, "print-extract", uid, _work, refund_op="process")
        return {"job_id": jid, "status": "pending"}

    # 本地路径(快)→ 同步直接返回(无 key 时即此;离线测试走这条)
    job_id = storage.new_job_id()
    storage.upload_path(job_id).write_bytes(raw)
    try:
        design, meta = extract_print_design(src)
    except ValueError as exc:
        refund(db, user, "process")
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    url, result = _save_outputs(job_id, design, meta)
    save_as_asset(db, user.id, design, "印花提取", url, source="generated")
    return {"job_id": job_id, **result}
