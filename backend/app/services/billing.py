"""计费扣点服务:点数定价 / 原子扣点 / 退点 / FastAPI 依赖工厂。"""
from __future__ import annotations
from fastapi import Depends, HTTPException
from sqlalchemy import update
from sqlalchemy.orm import Session
from ..db import get_db
from ..models_db import User
from ..auth import current_user


class InsufficientCredits(Exception):
    """余额不足,无法完成扣点。"""

    def __init__(self, op: str, cost: int, balance: int):
        self.op = op
        self.cost = cost
        self.balance = balance
        super().__init__(f"点数不足:操作 '{op}' 需 {cost} 点,当前余额 {balance} 点")


# 各操作的点数定价
# 注:title=1(走 AI 文本/识图,便宜);无 key/AI 失败时端点会退点 -> 本地兜底实际 0
COST: dict[str, int] = {"process": 2, "generate": 5, "edit": 4, "asset": 1, "video": 3, "title": 1}


def cost_of(op: str) -> int:
    """返回某操作的点数花费;未知 op 默认 1。"""
    return COST.get(op, 1)


def charge(db: Session, user: User, op: str) -> int:
    """原子扣点(P0-1):用条件 UPDATE 把余额校验下推到 DB,避免读-改-写竞态透支。

    SQL: UPDATE users SET credits=credits-:cost WHERE id=:id AND credits>=:cost
    rowcount==0 表示余额不足(无行被改),抛 InsufficientCredits。
    """
    cost = cost_of(op)
    res = db.execute(
        update(User)
        .where(User.id == user.id, User.credits >= cost)
        .values(credits=User.credits - cost)
    )
    if res.rowcount == 0:
        db.rollback()
        # 重新读取真实余额用于报错
        db.refresh(user)
        raise InsufficientCredits(op, cost, user.credits)
    db.commit()
    db.refresh(user)
    return user.credits


def refund(db: Session, user: User, op: str) -> int:
    """退点(P0-2):AI 调用失败后把预扣的点数原子退回。"""
    cost = cost_of(op)
    db.execute(update(User).where(User.id == user.id).values(credits=User.credits + cost))
    db.commit()
    db.refresh(user)
    return user.credits


def charge_for(op: str):
    """FastAPI 依赖工厂:返回一个既鉴权又扣点的依赖。

    用法:`@router.post(..., dependencies=[Depends(charge_for("process"))])`
    或    `def endpoint(user: User = Depends(charge_for("process"))): ...`
    余额不足时抛 HTTP 402;成功返回 User。
    """

    def _dep(user: User = Depends(current_user), db: Session = Depends(get_db)) -> User:
        try:
            charge(db, user, op)
        except InsufficientCredits as exc:
            raise HTTPException(status_code=402, detail=str(exc)) from exc
        return user

    return _dep
