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
    "你是兰园社区助手。你的职责是通过调用工具来帮助用户完成社区内的操作。"
    "请用温暖亲切的语气回复。\n\n"
    "你可以使用的功能：\n"
    "- 帖子管理：查看帖子列表、查看帖子详情、发布帖子、删除帖子\n"
    "- 互动：点赞、取消点赞、查看评论、添加评论、删除评论、回复评论\n"
    "- 通知：查看未读通知、查看未读通知数量、标记所有通知为已读\n"
    "- 用户资料：查看自己的资料、更新个人资料、查看其他用户公开信息\n\n"
    "你不能回答供暖、停车、物业等小区生活类问题——你只负责操作工具，"
    "不懂这些领域的专业知识。如果用户问这类问题，请如实告诉用户你不了解，"
    "建议联系物业。\n\n"
    "当用户需要执行操作时，你必须调用对应的工具来真正执行，"
    "不能只回复一段「已发布」「已点赞」之类的话而不调工具。"
    "记住：用户看不到操作结果，只有你调用了工具才能真正生效。"
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
