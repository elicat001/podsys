"""路由层公共工具:统一「读图失败即退点 + 400」范式(消除 4 个 router 的重复,评审 P1-3)。"""
from __future__ import annotations
import io
from fastapi import HTTPException
from PIL import Image
from sqlalchemy.orm import Session
from .models_db import User
from .services.billing import refund


def read_image_or_refund(raw: bytes, db: Session, user: User, op: str) -> Image.Image:
    """解码上传图片;失败则退回已预扣的 `op` 点数,再抛 400。

    适用于「单次预扣」端点(charge_for 扣 1 笔)。按张多扣的端点(如 variants)
    需自行处理退点笔数,勿用本函数。
    """
    try:
        im = Image.open(io.BytesIO(raw))
        im.load()
        return im
    except Exception as exc:  # noqa: BLE001
        refund(db, user, op)
        raise HTTPException(status_code=400, detail=f"无法读取图片: {exc}") from exc
