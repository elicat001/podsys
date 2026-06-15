#!/usr/bin/env python3
"""阶段三 retention CLI:跑一次本地产物缓存清理(释放应用盘)。

仅 s3 模式 + POD_S3_RETENTION_DAYS>0 才真正动手;否则打印跳过原因。
逻辑在 backend/app/services/retention.py。可挂每日 systemd timer(scripts/setup-retention.sh)。

手动跑(生产):
    cd /www/wwwroot/podsys/backend && ./.venv/bin/python ../scripts/retention.py
"""
import sys
from pathlib import Path

# 让本脚本能 import 到 backend/app(scripts/ 与 backend/ 同级)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.services.retention import run_retention  # noqa: E402

s = run_retention()
if s.get("skipped"):
    print(f"retention 跳过:backend={s.get('backend')}, days={s.get('days')} "
          f"(需 POD_STORAGE_BACKEND=s3 且 POD_S3_RETENTION_DAYS>0)")
else:
    print(f"retention 完成:扫描 {s['scanned']}、删本地 {s['deleted']}、"
          f"释放 {s['freed_bytes'] / 1048576:.1f}MB、近期保留 {s['kept_fresh']}、"
          f"无副本保留 {s['kept_no_copy']}、清空目录 {s['dirs_removed']}")
