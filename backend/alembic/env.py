"""Alembic 环境:复用 app 的 settings(DB 连接串来自 .env,不在 alembic.ini 硬编码密码)
+ 全部模型的 Base.metadata 做 autogenerate 对比。改表流程见 CLAUDE.md / alembic/README。"""
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context

# 让 alembic 能 import app.*(env.py 在 backend/alembic/,上两级是 backend/)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.db import Base, engine  # noqa: E402  (engine 已按 POD_DATABASE_URL 建好)

# 导入全部模型 → 把所有表注册进 Base.metadata(autogenerate 靠它和数据库对比)
from app import (  # noqa: E402,F401
    models_collect,
    models_db,
    models_shop,
    models_team,
    models_template,
)

config = context.config
# DB 连接串走应用配置(settings 读 .env / 环境变量),不写进 alembic.ini
config.set_main_option("sqlalchemy.url", settings.database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """离线模式(--sql):只用 URL 生成 SQL,不连库。"""
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """在线模式:复用 app 已建好的 engine 连库执行。"""
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
