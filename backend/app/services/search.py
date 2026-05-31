"""以图搜图:在用户自己的素材库里按相似度检索。

复用 phash 的结构哈希(dhash/hamming)与绝对颜色签名(color_sig/color_distance)。

综合相似度 sim 同时考虑结构与配色:
  - struct = similarity(dhash, a.dhash)         # 0..1,1=结构一致
  - color  = 1 - color_distance(chash, a.chash)/255  # 0..1,1=配色一致
  - sim    = 0.7*struct + 0.3*color             # 加权综合,单调:结构/配色越近 sim 越高

结构占主导(0.7)以贴近“同一张图/同构图”的检索直觉,配色(0.3)用于在结构接近时
区分不同配色版本。同一张图自身 struct=1、color=1 → sim=1.0,稳居首位。
"""
from __future__ import annotations

from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models_db import Asset
from . import phash

_W_STRUCT = 0.7
_W_COLOR = 0.3


def search_assets(db: Session, owner_id: int, image: Image.Image, top_k: int = 10) -> list[dict]:
    """对 owner 的 Asset 表算综合相似度,按 sim 降序、struct_distance 升序返回 top_k。

    返回每项:{asset_id, name, struct_distance, color_distance, similarity}。
    """
    q_dhash = phash.dhash(image)
    q_chash = phash.color_sig(image)

    # 排除回收站(deleted)素材,与 infringement 口径一致(评审 P1-1)
    rows = db.execute(
        select(Asset).where(Asset.owner_id == owner_id, Asset.deleted == False)  # noqa: E712
    ).scalars().all()

    scored: list[dict] = []
    for a in rows:
        ds = phash.hamming(q_dhash, a.dhash)
        dc = phash.color_distance(q_chash, a.chash)
        struct = phash.similarity(q_dhash, a.dhash)
        color = 1.0 - dc / 255.0
        sim = _W_STRUCT * struct + _W_COLOR * color
        scored.append({
            "asset_id": a.id,
            "name": a.name,
            "struct_distance": ds,
            "color_distance": round(dc, 4),
            "similarity": round(sim, 6),
        })

    # 主排序 sim 降序;同 sim 时结构距离小者优先
    scored.sort(key=lambda r: (-r["similarity"], r["struct_distance"]))
    return scored[: max(0, top_k)]
