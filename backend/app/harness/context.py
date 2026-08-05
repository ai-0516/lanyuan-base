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

换项目/换场景时：增删 PROMPT_SECTIONS 即可（section 顺序 = dict 定义顺序，Python 3.7+ 插入序），不改主逻辑。

缓存设计（对齐 Hermes 不变量 + 2026-08-05 snxly review 定 session 粒度）：
- Hermes 硬不变量：「不改变过去的上下文，新内容只追加在末尾」——
  system prompt 在对话（session）生命周期内 byte-stable；每轮变化的工具结果作为新消息追加。
- 缓存 key = session_id：session 内首次组装后冻结，memory_index 只参与首次组装，
  之后 session 内变化（如 LLM 主动 memory_add）不使缓存失效，
  新记忆下个 session 生效（保持前缀缓存命中，token 成本优先）。
- PROMPT_SECTIONS 是模块级静态定义，改动随进程重启生效（无运行时热更新机制），
  不纳入缓存 key（2026-08-05 删 sections_digest，见 PR #39 comment）。
- 历史 user 消息从 DB 读出原始内容，绝不改写。
"""

import json
from collections import OrderedDict

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
}


def assemble_system_prompt(context: dict) -> str:
    """按真实状态选择并拼接 sections（参考实现 s10：同 context → 同输出）

    - section 顺序 = PROMPT_SECTIONS 定义顺序（dict 插入序，Python 3.7+ 规范）
    - 始终加载：identity / tools / memory（说明）/ compression
    - 按需加载：memory_index 非空 → 追加索引段
    """
    parts: list[str] = []
    for name in PROMPT_SECTIONS:
        parts.append(PROMPT_SECTIONS[name])
        if name == "memory":
            memory_index = context.get("memory_index", "")
            if memory_index:
                parts.append(memory_index)
    return "\n\n".join(parts)


# session 粒度缓存：key=session_id → 组装结果。
# 手动 LRU（OrderedDict）：lru_cache 的参数即 key，无法表达
# 「组装输入（memory_index）参与组装但不参与 key」的冻结语义。
# 并发安全：get_system_prompt 是同步函数（无 await），FastAPI 中同一事件循环
# 单线程执行，OrderedDict 操作原子安全；非线程安全，勿在多线程中并发调用。
_SESSION_PROMPT_CACHE: "OrderedDict[str, str]" = OrderedDict()
_CACHE_MAXSIZE = 128


def get_system_prompt(context: dict = {}) -> str:
    """组装 System Prompt（session 粒度缓存）

    context 可含：
    - session_id：会话标识。有 → 按 session_id 缓存冻结；
      **无 → 不缓存**，每次按当前 context 现组装——没有会话上下文就没有
      「生命周期内字节稳定」可言，缓存无意义。
    - memory_index：组装输入，只参与首次组装，不参与缓存 key。

    缓存语义（2026-08-05 snxly review 定）：
    - session 内首次组装后**冻结**：memory_index 变化
      （如 LLM 主动 memory_add 使记忆索引变化）不使缓存失效——新记忆
      下个 session 生效。这是有意的：保持 system 字节稳定，前缀缓存
      持续命中，token 成本优先。
    - 缓存按 session_id LRU（maxsize=128），多会话交替各自命中，互不覆盖。
    - PROMPT_SECTIONS 是模块级静态定义，改动随进程重启生效（无运行时热更新
      机制），因此不纳入缓存 key（2026-08-05 删 sections_digest，见 PR #39）。
    """
    session_id = context.get("session_id")
    if not session_id:
        # 无会话上下文 → 没有「生命周期内字节稳定」可言，缓存无意义，每次现组装。
        # 生产路径（ai_service）总是传 session_id，此分支是接口兜底语义。
        # 不用固定默认值（如 "default"）：无 session 调用会共享缓存互相串扰；
        # 也不用随机 key 模拟 miss：隐晦，不如直白分支（2026-08-05 讨论定）。
        return assemble_system_prompt(context)
    cached = _SESSION_PROMPT_CACHE.get(session_id)
    if cached is not None:
        _SESSION_PROMPT_CACHE.move_to_end(session_id)
        return cached
    prompt = assemble_system_prompt(context)
    _SESSION_PROMPT_CACHE[session_id] = prompt
    _SESSION_PROMPT_CACHE.move_to_end(session_id)
    if len(_SESSION_PROMPT_CACHE) > _CACHE_MAXSIZE:
        _SESSION_PROMPT_CACHE.popitem(last=False)
    return prompt


# 兼容常量：默认 context（无记忆）的组装结果。
# 直接走纯组装函数，不经缓存——常量是「默认角色」的静态快照，与缓存无关。
SYSTEM_PROMPT = assemble_system_prompt({})


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
    session_id: str = "",
) -> list[dict]:
    """组装 DeepSeek 请求的 messages 数组

    System Prompt 在最前（按 section 运行时组装），历史消息在中间。
    用户消息在上一步已写入 DB，因此已包含在 history 中。
    user_message 参数保留用于未来扩展。

    记忆（2026-08-03 粒度设计 + 2026-08-05 session 冻结）：
    - 只注入记忆索引（build_memory_index 生成，全部记忆的 description）；
      不注入相关记忆（select_relevant 是动态选择，每轮结果可能不同，是缓存杀手）。
    - memory_index 在 session 首次组装 system prompt 时注入，之后**冻结**
      （缓存 key 不含 memory_index）——LLM 主动 memory_add 后新记忆不立即
      反映到当前 session 的 system prompt，下个 session 生效。
      这是有意的：保持 system 字节稳定 → 前缀缓存命中，token 成本优先。

    session_id：会话标识，并入 context 决定 system prompt 缓存粒度（同 session 冻结复用）。
    """
    system_content = get_system_prompt(
        {
            "session_id": session_id,
            "memory_index": memory_index,
        }
    )

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
