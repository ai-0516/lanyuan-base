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
# MySQL → 连接保活：MySQL wait_timeout=28800（8h）会关闭空闲连接，
# 连接池复用僵尸连接会报 "Lost connection to MySQL server during query"（#64）。
# pool_pre_ping 取连接前探活剔除死连接；pool_recycle 早于 wait_timeout 定期回收。
_pool_keepalive = (
    {"pool_pre_ping": True, "pool_recycle": 3600}
    if "sqlite" not in settings.DATABASE_URL
    else {}
)

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    connect_args=_connect_args,
    **_sqlite_pool,
    **_pool_keepalive,
)

# SQLite 默认不强制外键约束，必须显式开启 PRAGMA（MySQL 天然支持 FK）
if "sqlite" in settings.DATABASE_URL:

    @event.listens_for(engine.sync_engine, "connect")
    def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

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
