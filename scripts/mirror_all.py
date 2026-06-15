#!/usr/bin/env python3
"""一次性补传:把 MinIO 上线前就存在的本地存量产物全部镜像进对象存储(幂等,可重跑)。

仅 s3 模式生效。补完后这些老文件也有 MinIO 副本(可备份、将来可被 retention 清理释放盘)。
逻辑在 backend/app/storage.py 的 mirror_all()。

生产手动跑:
    cd /www/wwwroot/podsys/backend && ./.venv/bin/python ../scripts/mirror_all.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app import storage  # noqa: E402

s = storage.mirror_all()
if s.get("skipped"):
    print("跳过:非 s3 模式(需 POD_STORAGE_BACKEND=s3)")
else:
    print(f"补传完成:{s['jobs']} 个作业目录、{s['files']} 个文件已镜像进对象存储(幂等,可重跑)")
