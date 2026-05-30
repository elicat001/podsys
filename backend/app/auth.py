"""Auth: pbkdf2 password hashing (stdlib, no native build) + JWT sessions."""
from __future__ import annotations
import hashlib
import hmac
import os
from datetime import datetime, timedelta, timezone
import jwt
from fastapi import Depends, HTTPException, Header
from sqlalchemy.orm import Session
from .db import get_db
from .models_db import User
from .config import settings

_ALGO = "HS256"
_ITER = 200_000


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _ITER)
    return f"pbkdf2${_ITER}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, iters, salt_hex, dk_hex = stored.split("$")
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), int(iters))
        return hmac.compare_digest(dk.hex(), dk_hex)
    except Exception:  # noqa: BLE001
        return False


def make_token(user_id: int) -> str:
    payload = {
        "sub": str(user_id),
        "exp": datetime.now(timezone.utc) + timedelta(days=7),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=_ALGO)


def _decode(token: str) -> int:
    try:
        data = jwt.decode(token, settings.jwt_secret, algorithms=[_ALGO])
        return int(data["sub"])
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=401, detail="无效或过期的令牌") from exc


def current_user(authorization: str = Header(default=""), db: Session = Depends(get_db)) -> User:
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="缺少 Authorization: Bearer <token>")
    uid = _decode(authorization.split(" ", 1)[1])
    user = db.get(User, uid)
    if not user:
        raise HTTPException(status_code=401, detail="用户不存在")
    return user
