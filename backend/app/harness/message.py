"""消息持久化

职责：
- 用户消息写入 messages 表
- AI 回复写入 messages 表
- 更新会话的 updated_at 时间戳
"""

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Conversation, Message


async def save_user_message(
    db: AsyncSession,
    conversation_id: int,
    content: str,
) -> Message:
    """保存用户消息到 messages 表"""
    msg = Message(
        conversation_id=conversation_id,
        role="user",
        content=content,
    )
    db.add(msg)
    await db.flush()
    return msg


async def save_assistant_message(
    db: AsyncSession,
    conversation_id: int,
    content: str,
) -> Message:
    """保存 AI 回复到 messages 表"""
    msg = Message(
        conversation_id=conversation_id,
        role="assistant",
        content=content,
    )
    db.add(msg)
    await db.flush()
    return msg


async def touch_conversation(db: AsyncSession, conversation_id: int):
    """刷新会话的 updated_at（不修改其他字段）"""
    await db.execute(
        update(Conversation)
        .where(Conversation.id == conversation_id)
        .values()
    )
    await db.commit()
