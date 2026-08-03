"""上下文窗口管理

职责：
- 从数据库读取最近 N 条消息
- 组装 DeepSeek API 的 messages 数组（含 System Prompt）
- 跨会话记忆（#9）：索引常驻 SYSTEM + 相关记忆拼进最后一条 user 消息

缓存命中设计（review #7）：
- DeepSeek context caching 按消息序列前缀匹配，system prompt 任何变化都会让整段缓存失效
- 因此：memory_index 常驻 SYSTEM（记忆不变则文本不变，天然可缓存）；
  relevant_memories 拼进「最后一条 user 消息」——历史序列不变、仅最后一条变化，
  前缀缓存仍命中。
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
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


def build_memory_index(memories: list) -> str:
    """生成记忆索引文本（常驻 SYSTEM PROMPT）

    每行一条：类型 + 一句话描述（review #5：name 是 kebab-case 短标识，
    对 LLM 无语义增益，去掉），供模型了解用户有哪些记忆。
    """
    if not memories:
        return ""
    lines = []
    for m in memories[: settings.MEMORY_INDEX_LIMIT]:
        lines.append(f"- [{m.type}] {m.description}")
    return "你的记忆索引：\n" + "\n".join(lines)


def build_relevant_section(memories: list) -> str:
    """生成相关记忆完整内容（拼进最后一条 user 消息）

    review #6：保留 [type]、去掉 name（body 已是完整内容）。
    """
    if not memories:
        return ""
    parts = []
    for m in memories:
        parts.append(f"[{m.type}] {m.body}")
    return "与当前对话相关的记忆：\n" + "\n\n".join(parts)


def build_deepseek_messages(
    history: list[Message],
    user_message: str,
    memory_index: str = "",
    relevant_memories: list | None = None,
) -> list[dict]:
    """组装 DeepSeek 请求的 messages 数组

    System Prompt 在最前，历史消息在中间。
    用户消息在上一步已写入 DB，因此已包含在 history 中。
    user_message 参数保留用于未来扩展。

    记忆（#9）：
    - memory_index: 记忆索引文本，拼接进 SYSTEM PROMPT（常驻、稳定、可缓存）
    - relevant_memories: 与当前对话相关的完整记忆列表，
      拼接进「最后一条 user 消息」（历史序列不变，前缀缓存仍命中，review #7）
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

    # 相关记忆拼进最后一条 user 消息（不影响 system 前缀的缓存）
    relevant = build_relevant_section(relevant_memories or [])
    if relevant:
        for entry in reversed(messages):
            if entry["role"] == "user" and isinstance(entry.get("content"), str):
                entry["content"] = entry["content"] + "\n\n" + relevant
                break
    return messages
