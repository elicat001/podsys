"""侵权/查重:把新图的感知哈希与素材库比对,给出风险评级。"""
from __future__ import annotations
from dataclasses import dataclass
from PIL import Image
from sqlalchemy.orm import Session
from sqlalchemy import select
from . import phash
from ..models_db import Asset

# 结构 dHash(0..64 hamming) + 绝对颜色签名(0..255 MAD),双阈值避免"同形不同色"误报
STRUCT_DUP = 6        # 结构 <=6 视为同构
STRUCT_REVIEW = 12
COLOR_DUP = 18.0      # 平均色差 <=18 视为配色基本一致
COLOR_REVIEW = 45.0


@dataclass
class Match:
    asset_id: int
    name: str
    struct_distance: int
    color_distance: float
    similarity: float


def _risk(ds: int, dc: float) -> str:
    if ds <= STRUCT_DUP and dc <= COLOR_DUP:
        return "high"            # 结构+配色都接近 → 疑似盗图/重复
    if ds <= STRUCT_REVIEW and dc <= COLOR_REVIEW:
        return "review"          # 有一定相似 → 人工复核
    return "safe"


def check_image(db: Session, image: Image.Image, owner_id: int | None = None,
                exclude_asset_id: int | None = None) -> dict:
    dh = phash.dhash(image)
    ch = phash.color_sig(image)
    # 按租户隔离:只与该用户自己的素材库比对(避免越权探知他人素材 + 测试串扰)
    q = select(Asset)
    if owner_id is not None:
        q = q.where(Asset.owner_id == owner_id)
    rows = db.execute(q).scalars().all()
    matches: list[Match] = []
    worst = "safe"
    for a in rows:
        if exclude_asset_id and a.id == exclude_asset_id:
            continue
        ds = phash.hamming(dh, a.dhash)
        dc = phash.color_distance(ch, a.chash) if a.chash else 255.0
        r = _risk(ds, dc)
        if r != "safe":
            matches.append(Match(a.id, a.name, ds, round(dc, 1), round(phash.similarity(dh, a.dhash), 4)))
            if r == "high" or (r == "review" and worst != "high"):
                worst = r
    matches.sort(key=lambda m: (m.struct_distance, m.color_distance))
    return {
        "dhash": dh,
        "chash": ch,
        "risk": worst,
        "best_struct_distance": matches[0].struct_distance if matches else None,
        "best_color_distance": matches[0].color_distance if matches else None,
        "matches": [m.__dict__ for m in matches[:10]],
    }
