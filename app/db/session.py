"""数据库连接与 Session 管理。"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase

from app.core.config import settings

connect_args = {}
if settings.database_url.startswith("sqlite"):
    # SQLite 本地调试需要关闭同线程限制；MySQL 不需要。
    connect_args = {"check_same_thread": False}

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    echo=False,
    connect_args=connect_args,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    """FastAPI 依赖：每个请求创建一个 Session，请求结束后关闭。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
