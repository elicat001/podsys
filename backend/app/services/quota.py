"""我的空间深度 —— 存储配额统计。

按 owner 聚合 Asset 的数量/字节:未删除按 source 分类(采集 collected vs 素材 其它),
回收站(deleted=True)单独统计;给出演示配额上限 + 占比 + 是否超容。
"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..models_db import Asset, Job


def quota_bytes_limit() -> int:
    """每用户存储额度(字节),由 POD_USER_QUOTA_GB 决定(默认 1 GiB)。"""
    return int(settings.user_quota_gb * 1024 ** 3)


def _owner_job_ids(db: Session, owner_id: int) -> set[str]:
    """该用户名下所有作业目录的 job_id —— 取自 Job(owner)∪ Asset(owner,含回收站)。
    Asset.path 形如 /files/{job_id}/{name} 或磁盘路径 <outputs>/{job_id}/{name}。"""
    ids: set[str] = set()
    for (jid,) in db.execute(select(Job.id).where(Job.owner_id == owner_id)):
        if jid:
            ids.add(jid)
    out_root = settings.outputs_dir
    for (path,) in db.execute(select(Asset.path).where(Asset.owner_id == owner_id)):
        if not path:
            continue
        try:
            if path.startswith("/files/"):
                jid = path[len("/files/"):].split("/", 1)[0]
            else:                                   # 磁盘路径:取相对 outputs 的首段
                jid = Path(path).resolve().relative_to(out_root.resolve()).parts[0]
            if jid:
                ids.add(jid)
        except Exception:  # noqa: BLE001 — 解析不了就跳过
            pass
    return ids


def _dir_bytes(d: Path) -> int:
    """目录内所有文件字节(含缩略图缓存 .thumb_*、额外格式、源图预览、打样图等一切产物)。作业目录是平铺的。"""
    total = 0
    try:
        for f in d.iterdir():
            try:
                if f.is_file():
                    total += f.stat().st_size
            except OSError:
                pass
    except OSError:
        pass
    return total


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

    # 回收站:deleted=True(数量/字节明细仍按 Asset 行统计,给分类用)
    trash_cnt, trash_bytes = db.execute(
        select(
            func.count(Asset.id),
            func.coalesce(func.sum(Asset.size_bytes), 0),
        ).where(Asset.owner_id == owner_id, Asset.deleted == True)  # noqa: E712
    ).one()
    trash = {"count": int(trash_cnt), "bytes": int(trash_bytes)}

    # 真实占用:两种口径——
    # - s3 模式:本地盘只是「可被 retention 清掉的缓存」,磁盘游走会失真 → 以 DB(Asset.size_bytes)为准
    #   (= 未删 collected/material + 回收站 trash 三者之和,与上面 by_category/trash 同源,口径自洽)。
    # - local 模式:沿用磁盘游走——把该用户所有作业目录大小加起来(含额外格式/缩略图/源图/打样/未清回收站文件)。
    if (settings.storage_backend or "local").lower() == "s3":
        total_bytes = collected["bytes"] + material["bytes"] + trash["bytes"]
    else:
        total_bytes = sum(_dir_bytes(settings.outputs_dir / jid) for jid in _owner_job_ids(db, owner_id))
    quota_bytes = quota_bytes_limit()
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
