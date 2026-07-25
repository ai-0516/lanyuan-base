"""存储层 — 会话与消息的数据库操作

职责：
- 会话：创建/查找/复用/归属校验
- 消息：用户/AI 消息入库、会话时间刷新
"""

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Conversation, Message


# ── 会话 ─────────────────────────────────────────

async def get_or_create(db: AsyncSession, user_id: int) -> Conversation:
    """获取最近一条会话，没有则新建"""
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
    """校验 session 是否属于指定用户"""
    result = await db.execute(
        select(Conversation).where(
            Conversation.id == session_id,
            Conversation.user_id == user_id,
        )
    )
    return result.scalar_one_or_none()


# ── 消息 ─────────────────────────────────────────

async def save_user_message(db: AsyncSession, conversation_id: int, content: str) -> Message:
    """保存用户消息"""
    msg = Message(conversation_id=conversation_id, role="user", content=content)
    db.add(msg)
    await db.flush()
    return msg


async def save_assistant_message(db: AsyncSession, conversation_id: int, content: str) -> Message:
    """保存 AI 回复"""
    msg = Message(conversation_id=conversation_id, role="assistant", content=content)
    db.add(msg)
    await db.flush()
    return msg


async def touch_conversation(db: AsyncSession, conversation_id: int):
    """刷新会话 updated_at"""
    await db.execute(
        update(Conversation)
        .where(Conversation.id == conversation_id)
        .values()
    )
    await db.commit()
