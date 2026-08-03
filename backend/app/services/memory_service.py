"""记忆业务服务层（#9，review #3 分层）

对齐项目现有分层（post_service/comment_service/notification_service）：
- 本层承载记忆的增删查实现（超限合并、幂等删除语义）
- api/v1/memory.py 只保留路由与 @tool 定义，不写业务逻辑
- tool 场景（tool_registry 注入 db/user_id 后直接调函数）与 REST 场景
  统一走本层，两条路径行为一致
"""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.harness import memory as memory_harness
from app.models.user_memory import UserMemory

logger = logging.getLogger(__name__)


async def list_memories(db: AsyncSession, user_id: int) -> list[UserMemory]:
    """列出当前用户全部记忆（按 updated_at 倒序）"""
    return await memory_harness.list_all(db, user_id)


async def get_memory(db: AsyncSession, user_id: int, memory_id: int) -> UserMemory | None:
    """按 id 获取单条记忆（仅限本人）。不存在返回 None。"""
    return await memory_harness.get(db, user_id, memory_id)


async def add_memory(
    db: AsyncSession,
    user_id: int,
    *,
    name: str,
    type: str,
    description: str,
    body: str,
) -> UserMemory:
    """写入一条记忆。超限先合并腾空间，仍满抛 MemoryLimitError。"""
    return await memory_harness.add(
        db, user_id,
        name=name, type=type, description=description, body=body,
    )


async def delete_memory(db: AsyncSession, user_id: int, memory_id: int) -> bool:
    """删除一条记忆（仅限本人）。幂等：id 不存在也视为成功，返回是否实际删除。"""
    return await memory_harness.delete(db, user_id, memory_id)
