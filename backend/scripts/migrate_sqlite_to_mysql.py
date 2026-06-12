"""把现有 SQLite 数据搬到 MySQL(保留主键 + 外键顺序)。一次性脚本,可重跑。

用法(在 backend/ 目录,用 venv 的 python):
    # 目标库从 .env 的 POD_DATABASE_URL 读(推荐:先在 .env 配好 MySQL 再跑)
    ./.venv/Scripts/python.exe scripts/migrate_sqlite_to_mysql.py
    # 或显式指定目标 + 源:
    ./.venv/Scripts/python.exe scripts/migrate_sqlite_to_mysql.py \
        --dest "mysql+pymysql://podsys:pwd@127.0.0.1:3306/podsys?charset=utf8mb4" \
        --src  "sqlite:///data/podstudio.db" --truncate

要点:
- 源永远是 SQLite(默认 backend/data/podstudio.db),不受 POD_DATABASE_URL 影响;
- 目标必须是 MySQL(非 sqlite),否则拒绝;
- 按外键依赖顺序(Base.metadata.sorted_tables)建表 + 灌数据,保留原 ID;
- JSON/Boolean/DateTime 由 SQLAlchemy 列类型自动转换(读出 dict/bool/datetime,写回 MySQL 原样)；
- 目标已有数据时默认中止(防误覆盖);加 --truncate 先清空目标各表再灌。
"""
from __future__ import annotations

import argparse
import pathlib
import sys

# 让脚本能 import app.*(scripts/ 的上一级是 backend/)
_BACKEND = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

# Windows 控制台默认 GBK,打印 ✓/中文会 UnicodeEncodeError;强制 UTF-8 输出(Linux 本就 UTF-8,无副作用)
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

from sqlalchemy import create_engine, func, select, text  # noqa: E402

# 导入所有模型 → 把全部表注册进 Base.metadata(不触发任何 create_all)
from app import (  # noqa: E402,F401
    models_collect,
    models_db,
    models_shop,
    models_team,
    models_template,
    models_workflow,
)
from app.config import settings  # noqa: E402
from app.db import Base  # noqa: E402


def _default_src() -> str:
    return f"sqlite:///{(settings.data_dir / 'podstudio.db').as_posix()}"


def main() -> int:
    ap = argparse.ArgumentParser(description="SQLite → MySQL 数据迁移")
    ap.add_argument("--src", default=_default_src(), help="源(SQLite)连接串")
    ap.add_argument("--dest", default=settings.database_url, help="目标(MySQL)连接串;默认读 POD_DATABASE_URL")
    ap.add_argument("--truncate", action="store_true", help="目标已有数据时先清空各表再灌")
    args = ap.parse_args()

    if not args.dest or args.dest.startswith("sqlite"):
        print("✗ 目标必须是 MySQL 连接串(--dest 或 .env 的 POD_DATABASE_URL),且不能是 sqlite。")
        return 2
    if "sqlite" not in args.src:
        print("✗ 源应为 SQLite(--src)。")
        return 2

    src = create_engine(args.src, future=True)
    dest = create_engine(args.dest, future=True, pool_pre_ping=True)
    is_mysql = dest.dialect.name == "mysql"

    print(f"源 : {args.src}")
    print(f"目标: {args.dest.split('@')[-1]}  (dialect={dest.dialect.name})")

    # 1) 目标建表(含新加的索引/utf8mb4 由库默认字符集决定)
    Base.metadata.create_all(dest)

    tables = list(Base.metadata.sorted_tables)  # 父表在前,满足外键顺序

    # 2) 安全检查:目标非空 → 需 --truncate
    with dest.connect() as dconn:
        non_empty = []
        for t in tables:
            n = dconn.execute(select(func.count()).select_from(t)).scalar() or 0
            if n:
                non_empty.append((t.name, n))
    if non_empty and not args.truncate:
        print("✗ 目标库已有数据,为防误覆盖已中止。确认要重灌请加 --truncate:")
        for name, n in non_empty:
            print(f"    {name}: {n} 行")
        return 3

    # 3) 灌数据(关闭外键检查以免顺序/自引用问题;MySQL 插入显式 ID 会自动顶高 AUTO_INCREMENT)
    total = 0
    with src.connect() as sconn, dest.begin() as dconn:
        if is_mysql:
            dconn.execute(text("SET FOREIGN_KEY_CHECKS=0"))
        if args.truncate:
            for t in reversed(tables):  # 子表先删
                dconn.execute(t.delete())
        for t in tables:
            rows = [dict(r._mapping) for r in sconn.execute(t.select())]
            if rows:
                dconn.execute(t.insert(), rows)
            print(f"  {t.name:<24} {len(rows):>6} 行")
            total += len(rows)
        if is_mysql:
            dconn.execute(text("SET FOREIGN_KEY_CHECKS=1"))

    print(f"✓ 迁移完成,共 {total} 行,{len(tables)} 张表。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
