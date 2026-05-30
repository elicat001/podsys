"""计费:余额查询 + 充值(dev)。"""
from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from ..db import get_db
from ..models_db import User
from ..auth import current_user
from ..services.billing import COST
from ..config import settings

router = APIRouter(prefix="/api/billing", tags=["billing"])


class TopupBody(BaseModel):
    amount: int


@router.get("/balance")
def balance(user: User = Depends(current_user)):
    return {"credits": user.credits, "price_list": COST}


@router.post("/topup")
def topup(body: TopupBody, user: User = Depends(current_user), db: Session = Depends(get_db)):
    # P1-5:自助充值仅限 dev 模式;生产应关闭(POD_DEV_BILLING=false),改走真实支付/后台发放
    if not settings.dev_billing:
        raise HTTPException(status_code=403, detail="自助充值已禁用(请通过支付渠道充值)")
    if body.amount <= 0:
        raise HTTPException(status_code=400, detail="充值点数必须为正整数")
    user.credits += body.amount
    db.commit()
    return {"credits": user.credits}
