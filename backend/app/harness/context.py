"""上下文窗口管理 + System Prompt 运行时组装（适配 learn-claude-code s10）

职责：
- 从数据库读取最近消息
- 组装 DeepSeek API 的 messages 数组（含 System Prompt）
- System Prompt 按 section 运行时组装，按真实状态按需拼接，缓存避免重复组装

Section 设计（issue #11）：
- identity     始终加载    角色定义 + 领域边界 + 行为准则
- tools        始终加载    可用功能列表 + 使用说明
- memory       始终加载说明；调用方传 memory_index 时追加索引段（#9 跨会话记忆）
- compression  始终加载    #8 上下文压缩策略说明 + 占位符解读
- workspace    按需注入    调用方传非空才加载（社区对话场景默认无此信息，换场景时启用）

换项目/换场景时：增删 PROMPT_SECTIONS + _SECTION_ORDER 即可，不改主逻辑。

缓存设计（对齐 Hermes 不变量）：
- Hermes 硬不变量：「不改变过去的上下文，新内容只追加在末尾」——
  system prompt 在对话生命周期内 byte-stable；每轮变化的工具结果作为新消息追加。
- 因此：组装结果按 context（memory_index / workspace）确定性缓存
  （json.dumps sort_keys 做 key，对齐参考实现 s10 的 deterministic cache，
  不用 Python hash()——进程随机化且嵌套 dict 不可 hash）。
  context 不变 → 字节不变 → API 前缀缓存命中；记忆/会话状态变化 → 缓存失效重组装。
- 历史 user 消息从 DB 读出原始内容，绝不改写。
"""

import json
from functools import lru_cache

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Message

# ── Prompt Sections ──────────────────────────────────────────
# 每个 section 独立维护，修改不影响其他 section

PROMPT_SECTIONS = {
    "identity": (
        "你是兰园社区助手。你的职责是通过调用工具来帮助用户完成社区内的操作。"
        "请用温暖亲切的语气回复。\n\n"
        "你不能回答供暖、停车、物业等小区生活类问题——你只负责操作工具，"
        "不懂这些领域的专业知识。如果用户问这类问题，请如实告诉用户你不了解，"
        "建议联系物业。\n\n"
        "当用户需要执行操作时，你必须调用对应的工具来真正执行，"
        "不能只回复一段「已发布」「已点赞」之类的话而不调工具。"
        "记住：用户看不到操作结果，只有你调用了工具才能真正生效。"
    ),
    "tools": (
        "你可以使用的功能：\n"
        "- 帖子管理：查看帖子列表、查看帖子详情、发布帖子、删除帖子\n"
        "- 互动：点赞、取消点赞、查看评论、添加评论、删除评论、回复评论\n"
        "- 通知：查看未读通知、查看未读通知数量、标记所有通知为已读\n"
        "- 用户资料：查看自己的资料、更新个人资料、查看其他用户公开信息\n"
        "- 记忆：查看自己的记忆索引、查看单条记忆完整内容、添加记忆、删除记忆"
    ),
    "memory": (
        "跨会话记忆：用户的偏好和关注事项会自动保存在记忆中，"
        "新会话中若用户提及之前的事，应结合记忆中的内容回答。"
        "当用户在对话中明确表达长期偏好、身份信息或持续关注的事项时，"
        "可调用 memory_add 工具将其保存——最有价值的记忆是能让你"
        "在未来的对话中少问一次、少纠正一次的信息。"
        "只记录明确、稳定、有价值的信息；不要保存任务进度、对话结果、"
        "临时安排或一次性的对话内容，一周后就失效的信息不属于记忆。"
        "记忆是后台能力——不要主动向用户提及记忆机制，"
        "不要告诉用户「我可以帮你记住」，也不要主动询问「要不要记住」。"
    ),
    "compression": (
        "上下文压缩：对话历史过长时，较早的消息会被摘要或裁剪。"
        "如果用户询问较早对话的具体细节而摘要中缺失，"
        "请重新执行相关工具获取最新数据，不要凭空编造。"
    ),
    "workspace": "当前对话上下文：{workspace}",
}

# Section 组装顺序（稳定序 → 字节稳定 → 前缀缓存命中）
_SECTION_ORDER = ("identity", "tools", "memory", "compression", "workspace")


def assemble_system_prompt(context: dict) -> str:
    """按真实状态选择并拼接 sections（参考实现 s10：同 context → 同输出）

    - 始终加载：identity / tools / memory（说明）/ compression
    - 按需加载：memory_index 非空 → 追加索引段；workspace 非空 → 追加上下文段
    """
    parts: list[str] = []
    for name in _SECTION_ORDER:
        if name == "workspace":
            ws = context.get("workspace", "")
            if ws:
                parts.append(PROMPT_SECTIONS["workspace"].format(workspace=ws))
            continue
        parts.append(PROMPT_SECTIONS[name])
        if name == "memory":
            memory_index = context.get("memory_index", "")
            if memory_index:
                parts.append(memory_index)
    return "\n\n".join(parts)


def _context_key(context: dict) -> str:
    """确定性序列化做缓存 key（对齐参考实现：json.dumps，非 Python hash）"""
    return json.dumps(context, sort_keys=True, ensure_ascii=False, default=str)


@lru_cache(maxsize=128)
def _assemble_cached(context_key: str) -> str:
    return assemble_system_prompt(json.loads(context_key))


def get_system_prompt(context: dict | None = None) -> str:
    """组装 System Prompt（带缓存）

    缓存语义：context 不变 → 返回相同字符串（字节稳定，前缀缓存命中）；
    context 变化（记忆/会话/workspace 状态变化）→ 重组装。
    lru_cache 按 context_key 缓存，多用户交替请求时各自命中，互不覆盖。
    """
    return _assemble_cached(_context_key(context or {}))


# 兼容常量：默认 context（无记忆、无 workspace）的组装结果。
# 真实请求走 get_system_prompt(context)；此常量供测试与旧引用使用。
SYSTEM_PROMPT = get_system_prompt({})


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
    workspace: str = "",
) -> list[dict]:
    """组装 DeepSeek 请求的 messages 数组

    System Prompt 在最前（按 section 运行时组装），历史消息在中间。
    用户消息在上一步已写入 DB，因此已包含在 history 中。
    user_message 参数保留用于未来扩展。

    记忆（2026-08-03 粒度设计）：只注入记忆索引（build_memory_index 生成，
    全部记忆的 description）——静态内容，记忆不变则 system 字节不变，
    前缀缓存命中。不再注入相关记忆（select_relevant 是动态选择，每轮结果
    可能不同，是缓存杀手）。
    workspace：可选，换场景时传入当前对话上下文信息（社区对话默认空）。
    """
    system_content = get_system_prompt({"memory_index": memory_index, "workspace": workspace})

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
