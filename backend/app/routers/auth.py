"""注册 / 登录 / 当前用户。"""
from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import select
from ..db import get_db
from ..models_db import User
from ..auth import hash_password, verify_password, make_token, current_user

router = APIRouter(prefix="/api/auth", tags=["auth"])


class Credentials(BaseModel):
    email: str
    password: str


@router.post("/register")
def register(body: Credentials, db: Session = Depends(get_db)):
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
