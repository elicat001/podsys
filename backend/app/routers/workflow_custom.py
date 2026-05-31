"""自定义工作流路由 —— 暴露 step 元数据 + 运行任意 step 序列(对标灵图首页编辑器)。

独立 APIRouter(prefix /api/workflows),路径用 /steps 与 /run-custom,
不与 workflow.py 自带 router 的 GET ""/POST /run 冲突;由 main.py 一起 include。
"""
from __future__ import annotations
import json

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from ..db import get_db, SessionLocal
from ..models_db import User
from ..auth import current_user
from ..web_utils import read_image_or_refund
from ..services.billing import charge_for, refund
from ..services.jobs import create_job, run_job
from ..services.workflow import STEP_REGISTRY, STEP_META, list_steps, run_custom
from .. import storage

router = APIRouter(prefix="/api/workflows", tags=["workflow-custom"])

# P1:自定义编排的 step 数量上限(防 "extract,extract,…" 千次重复打满 CPU/磁盘)
MAX_CUSTOM_STEPS = 20


@router.get("/steps")
def get_steps(user: User = Depends(current_user)) -> list[dict]:
    """列出所有可用 step + 元数据(前端编辑器渲染「可用节点」)。"""
    return list_steps()


@router.post("/run-custom")
async def run_custom_workflow(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    steps: str = Form(...),
    params: str = Form("{}"),
    user: User = Depends(charge_for("process")),
    db: Session = Depends(get_db),
):
    """运行用户编排的任意 step 序列。异步:立即返回 job_id,前端轮询 /api/jobs/{id}。

    扣 process 点;空 steps / 非法 params JSON / 读图失败 / 非法 step 均退点 + 400。
    """
    # 1. 解析 steps(逗号分隔,去空白与空项)
    step_list = [s.strip() for s in steps.split(",") if s.strip()]
    if not step_list:
        refund(db, user, "process")
        raise HTTPException(status_code=400, detail="steps 不能为空")
    # P1:step 数量上限,防重复堆叠 DoS
    if len(step_list) > MAX_CUSTOM_STEPS:
        refund(db, user, "process")
        raise HTTPException(status_code=400, detail=f"step 数量超上限({MAX_CUSTOM_STEPS})")

    # 2. 解析 params(JSON)
    try:
        parsed_params = json.loads(params) if params else {}
        if not isinstance(parsed_params, dict):
            raise ValueError("params 必须是 JSON 对象")
    except (ValueError, json.JSONDecodeError) as exc:
        refund(db, user, "process")
        raise HTTPException(status_code=400, detail=f"无法解析 params: {exc}") from exc

    # 3. 校验 steps 全部已注册
    invalid = [s for s in step_list if s not in STEP_REGISTRY]
    if invalid:
        refund(db, user, "process")
        raise HTTPException(status_code=400, detail=f"非法 step: {', '.join(invalid)}")

    # 3b. P0:自定义编排只允许「离线 step」。AI step(needs_ai)若放进来,
    # 一次 process(2) 却可能触发 N 次 gpt-image 调用(variants 的 variant_n 倍),
    # 即 batch5 已修的 N 倍计费套利在新入口复活。AI 能力请走各自的计量端点
    # (/api/design-tools/variants 等,按张扣点)。
    ai_steps = [s for s in step_list if STEP_META.get(s, {}).get("needs_ai")]
    if ai_steps:
        refund(db, user, "process")
        raise HTTPException(
            status_code=400,
            detail=f"自定义工作流暂不支持 AI 步骤({', '.join(ai_steps)}),请用对应工具端点按量计费",
        )

    # 4. 读图(失败自动退点 + 400)
    raw = await file.read()
    src = read_image_or_refund(raw, db, user, "process")

    # 5. 建作业 + 后台执行
    job = create_job(db, "workflow", params={"steps": step_list}, owner_id=user.id)
    jid = job.id
    uid = user.id
    storage.upload_path(jid).write_bytes(raw)

    def _work() -> dict:
        try:
            return run_custom(src, step_list, jid, parsed_params)
        except Exception:
            # 后台失败也要退点(与同步路径一致),用独立 session
            s = SessionLocal()
            try:
                u = s.get(User, uid)
                if u:
                    refund(s, u, "process")
            finally:
                s.close()
            raise

    background_tasks.add_task(run_job, jid, _work)
    return {"job_id": jid, "status": "pending"}
