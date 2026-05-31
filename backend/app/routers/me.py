"""我的空间 —— 个人资源总览。"""
from __future__ import annotations
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from ..db import get_db
from ..auth import current_user
from ..models_db import User
from ..services.overview import overview

router = APIRouter(prefix="/api/me", tags=["me"])


@router.get("/overview")
def get_overview(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    return overview(db, user)
