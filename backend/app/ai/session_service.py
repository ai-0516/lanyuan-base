"""v2 会话服务（TECH_SPEC §3.2：会话组装 / MySQL 读写）

M3（issue #90）：session id = 纯 uuid（§6.3 身份查询插件演进，不再编码
user_id）；owner 映射写入 MySQL sessions 表 owner_user_id（§8.2）——
FastAPI 是身份权威（JWT 验证处），DSH 侧桥插件经内部身份端点查 owner。

表结构真源 = docs/dsh/TECH_SPEC.md §8.2。sessions 表由 DSH 侧
mysql-persistence 插件负责建（Node SCHEMA_DDL，IF NOT EXISTS）；本文件只
维护 owner 映射所需的单表幂等 DDL 兜底（backend 可能先于 DSH 启动写 owner，
表不存在时报错——双保险，与插件 DDL 同源，改表结构两处必须同步）。
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# sessions 表 DDL（仅 owner 映射所需的列；完整结构见插件 SCHEMA_DDL / §8.2）。
# 与 dsh/mysql-persistence/src/schema.ts 的 SCHEMA_DDL 同源，必须同步。
SESSION_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS sessions (
  id               VARCHAR(64)  NOT NULL,
  version          BIGINT       NOT NULL DEFAULT 0,
  created_at       BIGINT       NOT NULL DEFAULT 0,
  cwd              VARCHAR(1024) NULL,
  parent_session   VARCHAR(64)  NULL,
  seed_length      BIGINT       NULL,
  origin           VARCHAR(64)  NULL,
  delegation_depth BIGINT       NULL,
  agent_preset     VARCHAR(256) NULL,
  incarnation      CHAR(36)     NOT NULL,
  revision         BIGINT       NOT NULL DEFAULT 0,
  owner_user_id    BIGINT       NULL,
  PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""


def new_session_id() -> str:
    """生成 v2 纯 uuid session id（§6.3：不再编码 user_id，id 即身份）。"""
    return f"v2-{uuid.uuid4()}"


async def ensure_session_table(db: AsyncSession) -> None:
    """幂等建 sessions 表（backend 侧兜底；DSH 插件 open 时同款 IF NOT EXISTS）。"""
    await db.execute(text(SESSION_TABLE_DDL))
    await db.commit()


async def record_session_owner(db: AsyncSession, session_id: str, user_id: int) -> None:
    """写/更新 owner 映射（INSERT ... ON DUPLICATE KEY UPDATE，幂等）。

    FastAPI 是身份权威：每次 chat 请求都 upsert（同 session 多轮复用只一行）；
    DSH 插件的 header upsert 不覆盖 owner_user_id（两写入方互不干扰，§8.2）。
    """
    await ensure_session_table(db)
    await db.execute(
        text(
            "INSERT INTO sessions (id, version, created_at, incarnation, revision, owner_user_id)\n"
            "VALUES (:sid, 0, 0, :incarnation, 0, :owner)\n"
            "ON DUPLICATE KEY UPDATE owner_user_id = VALUES(owner_user_id)"
        ),
        {
            "sid": session_id,
            "incarnation": str(uuid.uuid4()),
            "owner": user_id,
        },
    )
    await db.commit()


async def get_session_owner(db: AsyncSession, session_id: str) -> int | None:
    """查 owner 映射（内部身份端点用）；无行/无 owner → None。"""
    await ensure_session_table(db)
    result = await db.execute(
        text("SELECT owner_user_id FROM sessions WHERE id = :sid"),
        {"sid": session_id},
    )
    row = result.first()
    if row is None or row[0] is None:
        return None
    return int(row[0])
