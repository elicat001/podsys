"""Database layer — SQLAlchemy 2.0 + MySQL 8(utf8mb4)。

项目已**全面转 MySQL**(不再支持 SQLite)。`POD_DATABASE_URL` 必须是 `mysql+pymysql://...`
连接串:dev/prod 在 `backend/.env` 配;测试由 `tests/conftest.py` 指向同库名加 `_test` 的隔离库。

- 连接池硬化:`pool_pre_ping` 取连接前先 ping,剔除被服务端 `wait_timeout` 掐掉的死连接
  (根治 "MySQL server has gone away");`pool_recycle=3600` 连接最多复用 1 小时即回收。
- 行级锁天生支持多 Celery worker 并发写 Job 状态(SQLite 那套 'database is locked' 已成历史)。
- 字符集 utf8mb4(标题含中文+emoji,必须 4 字节)由连接串 `?charset=utf8mb4` + 库默认字符集保证。
- 建表 + 索引由 `create_all` 负责;schema 再演进建议引入 Alembic(别再堆在线补列 hack)。
"""
from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import settings

settings.ensure_dirs()

DB_URL = settings.database_url
if not DB_URL or not DB_URL.startswith(("mysql", "mariadb")):
    raise RuntimeError(
        "POD_DATABASE_URL 必须是 MySQL 连接串(项目已全面转 MySQL,不再支持 SQLite)。"
        "例:mysql+pymysql://podsys:<pwd>@127.0.0.1:3306/podsys?charset=utf8mb4"
    )

engine = create_engine(
    DB_URL, future=True,
    pool_pre_ping=True, pool_recycle=3600, pool_size=10, max_overflow=20,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, class_=Session)


class Base(DeclarativeBase):
    pass


def init_db() -> None:
    from . import (
        models_collect,  # noqa: F401  (采集任务/采集图表)
        models_db,  # noqa: F401  (核心表,register mappers)
        models_shop,  # noqa: F401  (店铺表)
        models_team,  # noqa: F401  (团队资源:套图模板)
        models_template,  # noqa: F401  (刊登/导出模板表)
        models_workflow,  # noqa: F401  (保存的自定义工作流表)
    )
    Base.metadata.create_all(engine)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
