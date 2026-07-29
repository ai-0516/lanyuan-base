import os
import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import create_engine, pool

from alembic import context

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# add your model's MetaData object here
# for 'autogenerate' support
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.core.database import Base
# Import all models so alembic can detect them

target_metadata = Base.metadata

# ── 优先从 .env 读取 DATABASE_URL ──
env_path = Path(__file__).parent.parent / ".env"
db_url = None
if env_path.exists():
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line.startswith("DATABASE_URL="):
                db_url = line.split("=", 1)[1].strip().strip("\"'")
                break

if db_url is None:
    db_url = config.get_main_option("sqlalchemy.url")

# ── 异步驱动 → 同步驱动（Alembic 需要同步驱动）──
# SQLite:  sqlite+aiosqlite → sqlite
# MySQL:   mysql+asyncmy    → mysql+pymysql
# 其他数据库按需添加
if "aiosqlite" in db_url:
    db_url = db_url.replace("sqlite+aiosqlite", "sqlite")
elif "asyncmy" in db_url:
    db_url = db_url.replace("mysql+asyncmy", "mysql+pymysql")
elif "aiomysql" in db_url:
    db_url = db_url.replace("mysql+aiomysql", "mysql+pymysql")
# 注意：不要用 config.set_main_option()，它会触发 ConfigParser 的 % 插值
# 也不要尝试修改 get_section() 返回的 dict（那是深拷贝，改它无效）
# 下面的 run_migrations_online 直接使用 db_url 创建引擎

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def include_object(obj, name, type_, reflected, compare_to):
    """自动迁移时忽略 users 表的 created_at / updated_at（MySQL 自动管理，Model 不映射）"""
    if type_ == "column" and name in ("created_at", "updated_at") and obj.table.name == "users":
        return False
    return True


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=include_object,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    # 直接用 db_url 创建引擎，绕过 ConfigParser 的 % 插值问题
    connectable = create_engine(db_url, poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata,
            include_object=include_object,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
