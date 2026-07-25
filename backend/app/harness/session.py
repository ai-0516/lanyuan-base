"""会话管理

职责：
- 创建/查找/复用 AI 对话会话
- 校验 session 归属（用户隔离）
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Conversation


async def get_or_create(db: AsyncSession, user_id: int) -> Conversation:
    """获取最近一条会话，没有则新建

    策略：按 user_id 查询，按 updated_at DESC 取最近一条。
    如果没有任何会话，新建一条并 flush 获取 id。
    """
    stmt = (
        select(Conversation)
        .where(Conversation.user_id == user_id)
        .order_by(Conversation.updated_at.desc())
        .limit(1)
    )
    result = await db.execute(stmt)
    conv = result.scalar_one_or_none()

    if conv is None:
        conv = Conversation(user_id=user_id, title="")
        db.add(conv)
        await db.flush()

    return conv


async def verify_ownership(db: AsyncSession, session_id: int, user_id: int) -> Conversation | None:
    """校验 session 是否属于指定用户

    Returns:
        Conversation — 校验通过
        None — session 不存在或不属于该用户
    """
    result = await db.execute(
        select(Conversation).where(
            Conversation.id == session_id,
            Conversation.user_id == user_id,
        )
    )
    return result.scalar_one_or_none()
