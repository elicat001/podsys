"""阶段三:本地产物缓存清理(retention)—— 释放应用盘。

s3 模式下,本地盘只是「写缓存」,产物的存储 of record 在 MinIO。本模块把**超过 N 天**
(N=`POD_S3_RETENTION_DAYS`)且**对象存储已确认有副本**的本地产物文件删掉,腾出磁盘;
`/files` 端点遇到本地缺失会自动从 MinIO 回源,所以删了不影响访问。

红线:
- **仅 s3 模式 + N>0 才动手**,否则直接跳过(local 模式本地盘就是真相源,绝不能删)。
- **没确认 MinIO 有副本的文件绝不删**(防丢数据)。缩略图 `.thumb_*` 是可重生的派生缓存,按老化直接删。
- 判龄用文件 **mtime**(回源会用 os.replace 刷新 mtime → 近期访问过的文件自动"保鲜",不会反复删)。

由 `scripts/retention.py` CLI 调用,可挂每日 systemd timer(scripts/setup-retention.sh)。
"""
from __future__ import annotations

import logging
import time

from .. import storage
from ..config import settings

log = logging.getLogger(__name__)


def run_retention(now: float | None = None) -> dict:
    """清理一轮。返回统计 dict;跳过时含 {"skipped": True, ...}。now 可注入(测试用)。"""
    backend = (settings.storage_backend or "local").lower()
    days = settings.s3_retention_days
    if backend != "s3" or days <= 0:
        return {"skipped": True, "backend": backend, "days": days}

    now = time.time() if now is None else now
    cutoff = now - days * 86400
    out = settings.outputs_dir
    stats = {"skipped": False, "scanned": 0, "deleted": 0, "freed_bytes": 0,
             "kept_fresh": 0, "kept_no_copy": 0, "dirs_removed": 0}
    if not out.is_dir():
        return stats

    for job_dir in sorted(out.iterdir()):
        if not job_dir.is_dir():
            continue
        job_id = job_dir.name
        for f in list(job_dir.iterdir()):
            if not f.is_file():
                continue
            stats["scanned"] += 1
            try:
                st = f.stat()
            except OSError:
                continue
            if st.st_mtime >= cutoff:
                stats["kept_fresh"] += 1
                continue
            name = f.name
            # 缩略图可重生 → 直接删;其余必须确认 MinIO 有副本才删(防丢数据)
            if name.startswith(".thumb_") or storage.object_exists(job_id, name):
                try:
                    f.unlink()
                    stats["deleted"] += 1
                    stats["freed_bytes"] += st.st_size
                except OSError:
                    pass
            else:
                stats["kept_no_copy"] += 1
        # 清掉变空的作业目录
        try:
            if not any(job_dir.iterdir()):
                job_dir.rmdir()
                stats["dirs_removed"] += 1
        except OSError:
            pass

    log.info("retention 完成: %s", stats)
    return stats
