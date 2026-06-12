"""工作流编排 API:列出预设 + 一键运行(异步,走 Job 系统)。"""
from __future__ import annotations

import io
import json

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from PIL import Image
from sqlalchemy.orm import Session

from ..db import SessionLocal, get_db
from ..models_db import User
from ..services.billing import charge_for, refund
from ..services.jobs import create_job, run_job
from ..services.workflow import WORKFLOWS, list_workflows, run_workflow

router = APIRouter(prefix="/api/workflows", tags=["workflows"])


@router.get("")
def get_workflows():
    return list_workflows()


@router.post("/run")
async def run(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    workflow_id: str = Form(...),
    params: str = Form("{}"),
    user: User = Depends(charge_for("process")),
    db: Session = Depends(get_db),
):
    """上传输入图 + 选工作流 → 立即返回 job_id,后台串联执行。前端轮询 /api/jobs/{id}。"""
    if workflow_id not in WORKFLOWS:
        refund(db, user, "process")
        raise HTTPException(status_code=400, detail=f"未知工作流: {workflow_id}")
    try:
        params_dict = json.loads(params) if params else {}
        if not isinstance(params_dict, dict):
            raise ValueError("params 必须是 JSON 对象")
    except Exception as exc:  # noqa: BLE001
        refund(db, user, "process")
        raise HTTPException(status_code=400, detail=f"params 解析失败: {exc}") from exc
    try:
        src = Image.open(io.BytesIO(await file.read())); src.load()
    except Exception as exc:  # noqa: BLE001
        refund(db, user, "process")
        raise HTTPException(status_code=400, detail=f"无法读取图片: {exc}") from exc

    job = create_job(db, "workflow", params={"workflow_id": workflow_id, **params_dict},
                     owner_id=user.id)
    jid, uid = job.id, user.id

    def _work() -> dict:
        try:
            return run_workflow(src, workflow_id, jid, params_dict)
        except Exception:
            s = SessionLocal()
            try:
                u = s.get(User, uid)
                if u:
                    refund(s, u, "process")
            finally:
                s.close()
            raise

    background_tasks.add_task(run_job, jid, _work)
    return JSONResponse({"job_id": jid, "status": "pending", "workflow": workflow_id})
