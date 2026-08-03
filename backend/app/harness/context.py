"""上下文窗口管理

职责：
- 从数据库读取最近 N 条消息
- 组装 DeepSeek API 的 messages 数组（含 System Prompt）
- 跨会话记忆（2026-08-03 粒度设计）：只注入记忆索引（memory harness 的
  build_memory_index 生成），本模块负责拼接进 SYSTEM PROMPT

缓存命中设计（对齐 Hermes 不变量）：
- Hermes 硬不变量：「不改变过去的上下文，新内容只追加在末尾」——
  system prompt 在对话生命周期内 byte-stable；每轮变化的工具结果作为新消息追加。
- 因此：memory_index（全部记忆 description）拼进 SYSTEM PROMPT，只随记忆内容
  变化（记忆只在 session 结束 /new 时抽取，session 内不变 → 字节不变 → 前缀缓存命中）；
  历史 user 消息从 DB 读出原始内容，绝不改写。
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
    "- 用户资料：查看自己的资料、更新个人资料、查看其他用户公开信息\n"
    "- 记忆：查看自己的记忆、添加记忆、删除记忆\n\n"
    "你不能回答供暖、停车、物业等小区生活类问题——你只负责操作工具，"
    "不懂这些领域的专业知识。如果用户问这类问题，请如实告诉用户你不了解，"
    "建议联系物业。\n\n"
    "当用户需要执行操作时，你必须调用对应的工具来真正执行，"
    "不能只回复一段「已发布」「已点赞」之类的话而不调工具。"
    "记住：用户看不到操作结果，只有你调用了工具才能真正生效。\n\n"
    "跨会话记忆：用户的偏好和关注事项会自动保存在记忆中，"
    "新会话中若用户提及之前的事，应结合记忆中的内容回答。"
    "记忆是后台能力——不要主动向用户提及记忆机制，"
    "不要告诉用户「我可以帮你记住」，也不要主动询问「要不要记住」。"
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
    memory_index: str = "",
) -> list[dict]:
    """组装 DeepSeek 请求的 messages 数组

    System Prompt 在最前，历史消息在中间。
    用户消息在上一步已写入 DB，因此已包含在 history 中。
    user_message 参数保留用于未来扩展。

    记忆（2026-08-03 粒度设计）：只注入记忆索引（build_memory_index 生成，
    全部记忆的 description）——静态内容，记忆不变则 system 字节不变，
    前缀缓存命中。不再注入相关记忆（select_relevant 是动态选择，每轮结果
    可能不同，是缓存杀手）。
    """
    import json

    system_content = SYSTEM_PROMPT
    if memory_index:
        system_content += "\n\n" + memory_index

    messages: list[dict] = [{"role": "system", "content": system_content}]
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
