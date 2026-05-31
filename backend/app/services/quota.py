"""我的空间深度 —— 存储配额统计。

按 owner 聚合 Asset 的数量/字节:未删除按 source 分类(采集 collected vs 素材 其它),
回收站(deleted=True)单独统计;给出演示配额上限 + 占比 + 是否超容。
"""
from __future__ import annotations

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from ..models_db import Asset

# 演示配额上限(settings 未配置时的常量):2 GiB
QUOTA_BYTES = 2 * 1024 ** 3


def usage(db: Session, owner_id: int) -> dict:
    """统计某用户的存储用量。

    返回:
        {
          quota_bytes, used_bytes, percent, over,
          by_category: {collected: {count, bytes}, material: {count, bytes}},
          trash: {count, bytes},
        }
    """
    # 未删除资产:按 source 是否为 collected 分两类聚合
    rows = db.execute(
        select(
            Asset.source,
            func.count(Asset.id),
            func.coalesce(func.sum(Asset.size_bytes), 0),
        )
        .where(Asset.owner_id == owner_id, Asset.deleted == False)  # noqa: E712
        .group_by(Asset.source)
    ).all()

    collected = {"count": 0, "bytes": 0}
    material = {"count": 0, "bytes": 0}
    for source, cnt, byts in rows:
        bucket = collected if source == "collected" else material
        bucket["count"] += int(cnt)
        bucket["bytes"] += int(byts)

    active_bytes = collected["bytes"] + material["bytes"]

    # 回收站:deleted=True
    trash_cnt, trash_bytes = db.execute(
        select(
            func.count(Asset.id),
            func.coalesce(func.sum(Asset.size_bytes), 0),
        ).where(Asset.owner_id == owner_id, Asset.deleted == True)  # noqa: E712
    ).one()
    trash = {"count": int(trash_cnt), "bytes": int(trash_bytes)}

    total_bytes = active_bytes + trash["bytes"]
    quota_bytes = QUOTA_BYTES
    percent = round(total_bytes / quota_bytes * 100, 2) if quota_bytes else 0.0
    over = total_bytes > quota_bytes

    return {
        "quota_bytes": quota_bytes,
        "used_bytes": total_bytes,
        "percent": percent,
        "over": over,
        "by_category": {"collected": collected, "material": material},
        "trash": trash,
    }
