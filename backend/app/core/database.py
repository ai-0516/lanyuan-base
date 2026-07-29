"""SQLAlchemy 异步数据库引擎和会话管理"""

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

# SQLite → 单连接池（pool_size=1, 无溢出）完全串行化，避免并发写锁；
# MySQL → 默认池（10 连接 + 10 溢出）
_connect_args = {"timeout": 15} if "sqlite" in settings.DATABASE_URL else {}
_sqlite_pool = (
    {"pool_size": 1, "max_overflow": 0}
    if "sqlite" in settings.DATABASE_URL
    else {"pool_size": 10, "max_overflow": 10}
)

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    connect_args=_connect_args,
    **_sqlite_pool,
)

# 为 SQLite 启用 WAL 模式（允许多连接并发读 + 串行写）
if "sqlite" in settings.DATABASE_URL:

    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, connection_record):
        import sqlite3

        # AsyncAdapt_aiosqlite_connection._connection → aiosqlite.Connection
        # aiosqlite.Connection._conn → 原始 sqlite3.Connection
        aiosqlite_conn = getattr(dbapi_connection, "_connection", dbapi_connection)
        raw = getattr(aiosqlite_conn, "_conn", aiosqlite_conn)
        if isinstance(raw, sqlite3.Connection):
            raw.execute("PRAGMA journal_mode=WAL")
            raw.execute("PRAGMA busy_timeout=15000")

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncSession:
    """FastAPI 依赖注入：获取数据库会话"""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db():
    """创建所有表（开发环境用）"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def close_db():
    """关闭数据库连接"""
    await engine.dispose()
