"""把工具产物登记进素材库(Asset)—— 打通「工具产出 → 我的空间 / 以图搜图 / 商品库」。

此前各工具只把图写到 /files,从不入库,导致我的空间/搜图/商品库永远是空架子。
本函数在产物保存后登记一条 Asset(含感知哈希,供查重/以图搜图),让那些版块真正有数据。
"""
from __future__ import annotations

import io

from PIL import Image
from sqlalchemy.orm import Session

from ..models_db import Asset
from . import phash


def save_as_asset(db: Session, owner_id: int, image: Image.Image, name: str,
                  path: str, source: str = "generated", size_bytes: int = 0) -> Asset | None:
    """登记一条素材。失败不抛(不能因为入库失败而毁掉工具主流程)。"""
    try:
        if size_bytes <= 0:
            buf = io.BytesIO(); image.convert("RGBA").save(buf, "PNG"); size_bytes = buf.tell()
        a = Asset(
            owner_id=owner_id, name=name, path=str(path),
            dhash=phash.dhash(image), chash=phash.color_sig(image),
            source=source, risk="unknown", size_bytes=size_bytes,
        )
        db.add(a); db.commit(); db.refresh(a)
        return a
    except Exception:  # noqa: BLE001
        db.rollback()
        return None
