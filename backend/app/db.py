"""Database layer — SQLAlchemy 2.0 + SQLite (swap URL for Postgres in prod)."""
from __future__ import annotations
from collections.abc import Iterator
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker, Session
from .config import settings

settings.ensure_dirs()
DB_URL = f"sqlite:///{(settings.data_dir / 'podstudio.db').as_posix()}"

engine = create_engine(DB_URL, connect_args={"check_same_thread": False}, future=True)
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
    from . import models_db  # noqa: F401  (register mappers)
    from . import models_collect  # noqa: F401  (采集任务/采集图表 — batch7)
    from . import models_shop  # noqa: F401  (店铺表 — batch7)
    from . import models_workflow  # noqa: F401  (保存的自定义工作流表 — batch8)
    from . import models_template  # noqa: F401  (刊登/导出模板表 — batch10)
    _migrate_columns()  # 先给老表补列(create_all 不补列)
    Base.metadata.create_all(engine)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
