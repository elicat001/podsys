"""注册 / 登录 / 当前用户。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import current_user, hash_password, make_token, verify_password
from ..config import settings
from ..db import get_db
from ..models_db import User
from ..ratelimit import register_limiter

router = APIRouter(prefix="/api/auth", tags=["auth"])


class Credentials(BaseModel):
    email: str
    password: str


@router.post("/register")
def register(body: Credentials, request: Request, db: Session = Depends(get_db)):
    # 注册总开关(测试阶段对外关闭):后端硬堵,不只依赖前端隐藏入口
    if not settings.register_enabled:
        raise HTTPException(status_code=403, detail="测试阶段,功能暂不支持")
    # 每 IP 注册限流(评审 P0-3:堵 guest 清缓存重刷点)
    ip = request.client.host if request.client else "unknown"
    if not register_limiter.allow(f"reg:{ip}", settings.register_rate_limit,
                                  settings.register_rate_window_sec):
        raise HTTPException(status_code=429, detail="注册过于频繁,请稍后再试")
    if db.execute(select(User).where(User.email == body.email)).scalar_one_or_none():
        raise HTTPException(status_code=409, detail="邮箱已注册")
    user = User(email=body.email, password_hash=hash_password(body.password))
    db.add(user); db.commit(); db.refresh(user)
    return {"token": make_token(user.id), "user_id": user.id, "credits": user.credits}


@router.post("/login")
def login(body: Credentials, db: Session = Depends(get_db)):
    user = db.execute(select(User).where(User.email == body.email)).scalar_one_or_none()
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="邮箱或密码错误")
    return {"token": make_token(user.id), "user_id": user.id, "credits": user.credits}


@router.get("/me")
def me(user: User = Depends(current_user)):
    return {"user_id": user.id, "email": user.email, "credits": user.credits}
