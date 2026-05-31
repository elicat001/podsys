"""我的工作流:用户保存/列出/查看/删除自定义工作流(均 owner 隔离)。"""
from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import select
from ..db import get_db
from ..models_workflow import SavedWorkflow
from ..models_db import User
from ..auth import current_user
from ..services.workflow import STEP_REGISTRY

router = APIRouter(prefix="/api/my-workflows", tags=["my-workflows"])


class WorkflowIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)  # P2:名称非空、限长
    steps: list[str] = Field(max_length=50)
    params: dict = Field(default_factory=dict)


@router.post("")
def create_workflow(body: WorkflowIn, user: User = Depends(current_user),
                    db: Session = Depends(get_db)):
    if not body.steps:
        raise HTTPException(status_code=400, detail="steps 不能为空")
    # P1:校验 step 合法,坏数据不入库(否则运行时才报错)
    invalid = [s for s in body.steps if s not in STEP_REGISTRY]
    if invalid:
        raise HTTPException(status_code=400, detail=f"非法 step: {', '.join(invalid)}")
    wf = SavedWorkflow(owner_id=user.id, name=body.name, steps=body.steps, params=body.params)
    db.add(wf); db.commit(); db.refresh(wf)
    return {"id": wf.id, "name": wf.name}


@router.get("")
def list_workflows(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.execute(
        select(SavedWorkflow)
        .where(SavedWorkflow.owner_id == user.id)
        .order_by(SavedWorkflow.created_at.desc())
    ).scalars().all()
    return [
        {"id": w.id, "name": w.name, "steps": w.steps, "params": w.params,
         "created_at": w.created_at.isoformat() if w.created_at else None}
        for w in rows
    ]


@router.get("/{wf_id}")
def get_workflow(wf_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    wf = db.get(SavedWorkflow, wf_id)
    if not wf or wf.owner_id != user.id:
        raise HTTPException(status_code=404, detail="工作流不存在")
    return {"id": wf.id, "name": wf.name, "steps": wf.steps, "params": wf.params,
            "created_at": wf.created_at.isoformat() if wf.created_at else None}


@router.delete("/{wf_id}")
def delete_workflow(wf_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    wf = db.get(SavedWorkflow, wf_id)
    if not wf or wf.owner_id != user.id:
        raise HTTPException(status_code=404, detail="工作流不存在")
    db.delete(wf); db.commit()
    return {"deleted": True}
