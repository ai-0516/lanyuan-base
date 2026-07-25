"""上下文窗口管理

职责：
- 从数据库读取最近 N 条消息
- 组装 DeepSeek API 的 messages 数组（含 System Prompt）
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Message

# 作为 DeepSeek 上下文的消息数
CONTEXT_LIMIT = 20

# System Prompt — 决定 AI 的角色和行为
SYSTEM_PROMPT = (
    "你是兰园社区助手，帮助小区业主解答供暖、停车等小区生活问题。"
    "请用温暖亲切的语气回复。"
    "当用户需要发布帖子时，使用 create_post 工具。"
)


async def get_recent_messages(
    db: AsyncSession,
    conversation_id: int,
    limit: int = CONTEXT_LIMIT,
) -> list[Message]:
    """获取会话中最早 N 条消息（按 created_at ASC）

    注：limit 取最早 N 条，不是最近 N 条。
    因为消息按正序插入，limit 20 取的是最早 20 轮对话。
    后续可优化为滑动窗口。
    """
    stmt = (
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.asc())
        .limit(limit)
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


def build_deepseek_messages(
    history: list[Message],
    user_message: str,
) -> list[dict]:
    """组装 DeepSeek 请求的 messages 数组

    System Prompt 在最前，历史消息在中间，当前用户消息在最后。
    """
    messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
    for m in history:
        messages.append({"role": m.role, "content": m.content})
    return messages
