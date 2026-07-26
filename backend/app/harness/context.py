"""上下文窗口管理

职责：
- 从数据库读取最近 N 条消息
- 组装 DeepSeek API 的 messages 数组（含 System Prompt）
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Message

# System Prompt — 决定 AI 的角色和行为
SYSTEM_PROMPT = (
    "你是兰园社区助手，帮助小区业主解答供暖、停车等小区生活问题。"
    "请用温暖亲切的语气回复。"
    "当用户需要发布帖子时，你必须调用 create_post 工具来真正发布，"
    "不能只回复一段「已发布」的文字。记住：用户看不到帖子内容，"
    "只有你调用工具才能真正发出去。"
)


async def get_recent_messages(
    db: AsyncSession,
    conversation_id: int,
) -> list[Message]:
    """获取会话全部消息（按时间正序）

    全部消息都发给 LLM，不截断。上下文压缩由专门的模块（如 session 旋转）处理。
    """
    stmt = (
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.asc(), Message.id.asc())
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


def build_deepseek_messages(
    history: list[Message],
    user_message: str,
) -> list[dict]:
    """组装 DeepSeek 请求的 messages 数组

    System Prompt 在最前，历史消息在中间。
    用户消息在上一步已写入 DB，因此已包含在 history 中。
    user_message 参数保留用于未来扩展。
    """
    import json
    messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
    for m in history:
        entry: dict = {"role": m.role}
        if m.role == "tool":
            entry["tool_call_id"] = m.tool_call_id
            entry["content"] = m.content
        elif m.role == "assistant" and m.tool_calls:
            entry["content"] = m.content or None
            entry["tool_calls"] = json.loads(m.tool_calls)
        else:
            entry["content"] = m.content
        messages.append(entry)
    return messages
