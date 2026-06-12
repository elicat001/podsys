"""Database layer — SQLAlchemy 2.0。后端可配置:默认 SQLite,设 POD_DATABASE_URL 切 MySQL。

- SQLite(默认/测试):单文件 + WAL 并发硬化,离线零依赖。
- MySQL(dev/prod):连接池硬化(pool_pre_ping/recycle)避免闲置断连;行级锁天生支持
  多 Celery worker 并发写 Job 状态(SQLite 那套 'database is locked' 的痛点自然消失)。
  字符集走 utf8mb4(标题含中文+emoji,必须 4 字节),在连接串里 ?charset=utf8mb4 指定。
"""
from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import settings

settings.ensure_dirs()

# 留空 → SQLite(锚定 backend/data/podstudio.db);否则用配置的 URL(MySQL/Postgres)。
DB_URL = settings.database_url or f"sqlite:///{(settings.data_dir / 'podstudio.db').as_posix()}"
IS_SQLITE = DB_URL.startswith("sqlite")

if IS_SQLITE:
    # timeout=10:拿不到写锁时最多等 10s(而非立刻报错),配合多 worker 并发写 Job 状态。
    engine = create_engine(DB_URL, connect_args={"check_same_thread": False, "timeout": 10}, future=True)

    @event.listens_for(engine, "connect")
    def _sqlite_concurrency_pragmas(dbapi_conn, _rec):
        """SQLite 并发硬化(多 Celery worker 同时写 + 前端高频轮询读):
        WAL 让『读不阻塞写、写不阻塞读』,大幅减少 'database is locked';busy_timeout 兜底锁等待。
        synchronous=NORMAL 在 WAL 下安全且更快(仅极端断电可能丢最后一两个事务,本场景可接受)。"""
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA busy_timeout=10000")
        cur.close()
else:
    # MySQL/Postgres:长连接服务必备的池硬化。
    # pool_pre_ping:取连接前先 ping,自动剔除被服务端闲置超时(wait_timeout)掐掉的死连接,
    #   根治 "MySQL server has gone away"。pool_recycle=3600:连接最多复用 1 小时就回收(< wait_timeout)。
    engine = create_engine(
        DB_URL, future=True,
        pool_pre_ping=True, pool_recycle=3600, pool_size=10, max_overflow=20,
    )


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, class_=Session)


class Base(DeclarativeBase):
    pass


# 轻量在线迁移:项目无 Alembic,而 create_all 只建新表、不会给**已存在**的表补列。
# 给老表新增可空列时,在此登记 (表, 列, SQL 类型),启动时按需 ALTER(幂等:先查现有列)。
# SQLite 的 ALTER TABLE ADD COLUMN 对存量行安全(新列填 NULL/默认)。
_ADDED_COLUMNS: list[tuple[str, str, str]] = [
    ("jobs", "tool_id", "VARCHAR(32) DEFAULT ''"),
    ("jobs", "started_at", "DATETIME"),
    ("jobs", "finished_at", "DATETIME"),
    ("users", "org_id", "INTEGER DEFAULT 1"),   # 团队资源共享维度;现有用户统一到 org 1
    # batch13:采集→选择→同步,给采集图补富元数据 + 同步状态列
    ("collected_images", "price", "VARCHAR(32) DEFAULT ''"),
    ("collected_images", "rating", "VARCHAR(16) DEFAULT ''"),
    ("collected_images", "source_url", "VARCHAR(1024) DEFAULT ''"),
    ("collected_images", "synced", "BOOLEAN DEFAULT 0"),
    ("collected_images", "synced_asset_id", "INTEGER"),
    ("collected_images", "asset_url", "VARCHAR(1024) DEFAULT ''"),
]


def _migrate_columns() -> None:
    """对已存在的表补齐 _ADDED_COLUMNS 里缺失的列(幂等)。新库由 create_all 直接建全,跳过。"""
    insp = inspect(engine)
    existing = set(insp.get_table_names())
    with engine.begin() as conn:
        for table, col, coltype in _ADDED_COLUMNS:
            if table not in existing:
                continue  # 新库:create_all 已建含新列的表
            cols = {c["name"] for c in insp.get_columns(table)}
            if col not in cols:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {coltype}"))


def init_db() -> None:
    from . import (
        models_collect,  # noqa: F401  (采集任务/采集图表 — batch7)
        models_db,  # noqa: F401  (register mappers)
        models_shop,  # noqa: F401  (店铺表 — batch7)
        models_team,  # noqa: F401  (团队资源:套图模板)
        models_template,  # noqa: F401  (刊登/导出模板表 — batch10)
        models_workflow,  # noqa: F401  (保存的自定义工作流表 — batch8)
    )
    _migrate_columns()  # 先给老表补列(create_all 不补列)
    Base.metadata.create_all(engine)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
