"""Database layer — SQLAlchemy 2.0 + SQLite (swap URL for Postgres in prod)."""
from __future__ import annotations
from collections.abc import Iterator
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker, Session
from .config import settings

settings.ensure_dirs()
DB_URL = f"sqlite:///{(settings.data_dir / 'podstudio.db').as_posix()}"

engine = create_engine(DB_URL, connect_args={"check_same_thread": False}, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, class_=Session)


class Base(DeclarativeBase):
    pass


def init_db() -> None:
    from . import models_db  # noqa: F401  (register mappers)
    from . import models_collect  # noqa: F401  (采集任务/采集图表 — batch7)
    from . import models_shop  # noqa: F401  (店铺表 — batch7)
    Base.metadata.create_all(engine)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
