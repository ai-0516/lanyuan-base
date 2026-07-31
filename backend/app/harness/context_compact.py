"""上下文压缩管线（适配 learn-claude-code s08 Context Compact）

多层压缩策略，便宜的先跑（0 API），贵的后跑（1 API）：
    L1 snip_compact      — 消息数超限 → 裁剪中间，保留头部(前3) + 尾部
    L2 micro_compact     — 保留最近 N 组工具结果，更早的占位
    L4 compact_history   — token 估算超限 → LLM 摘要（1 次 API）
    reactive_compact     — 413 应急：保留尾部 + 头部摘要，摘要失败强裁剪兜底

设计约定：
- 只操作内存 messages（DeepSeek/OpenAI 兼容格式），不碰 DB
- 压缩管线由 AIAgent 每 turn 调 LLM 前调用；413 由 streaming 层触发 reactive
- system 消息（数组第一条）始终保留，不参与裁剪计数
- assistant(tool_calls) 与其后的 tool 结果消息配对，裁剪/占位不拆散
"""

import json
import logging

logger = logging.getLogger(__name__)

# ── 阈值常量 ─────────────────────────────────────
MAX_MESSAGES = 50            # L1: 消息数超过则裁剪中间（保留头部 3 + 尾部 47）
KEEP_HEAD = 3                # L1: 头部保留条数
KEEP_RECENT_TOOL_RESULTS = 3 # L2: 保留最近 N 个 tool 结果
TOOL_RESULT_SNIP_LENGTH = 120    # L2: 超过此长度的旧 tool 结果才占位
TOOL_RESULT_PLACEHOLDER = "[Earlier tool result compacted. Re-run if needed.]"
COMPACT_THRESHOLD = 60_000   # L4: 字符数估算阈值（≈30K~50K token，DeepSeek 无官方 tokenizer）
REACTIVE_KEEP_TAIL = 5       # reactive: 尾部保留条数
SUMMARY_INPUT_LIMIT = 80_000  # 发给摘要 LLM 的对话截断（字符）

SUMMARY_PROMPT = (
    "CRITICAL: Respond with TEXT ONLY. Do NOT call any tools.\n\n"
    "请用中文总结这段社区助手对话，使对话可以继续。需要保留：\n"
    "1. 用户当前的目标 / 正在进行的操作\n"
    "2. 关键决定和已执行的操作（如已发布的帖子、已完成的点赞等）\n"
    "3. 用户表达的偏好和约束\n"
    "4. 尚未完成的剩余事项\n"
    "请精炼但具体，不要泛泛而谈。\n\n"
    "对话内容：\n"
)


# ── 消息格式判断（OpenAI/DeepSeek 兼容格式） ─────

def _is_tool_call_message(msg: dict) -> bool:
    """assistant 消息且带非空 tool_calls → 工具调用消息"""
    return (
        msg.get("role") == "assistant"
        and bool(msg.get("tool_calls"))
    )


def _is_tool_result_message(msg: dict) -> bool:
    """role=tool 消息 → 工具结果消息"""
    return msg.get("role") == "tool"


def _split_system(messages: list[dict]) -> tuple[list[dict], list[dict]]:
    """分离数组头部的 system 消息，返回 (system 段, 其余消息)"""
    if messages and messages[0].get("role") == "system":
        return [messages[0]], list(messages[1:])
    return [], list(messages)


def estimate_tokens(messages: list[dict]) -> int:
    """字符数近似估算 token（DeepSeek 无官方 tokenizer，保守偏大）"""
    return len(json.dumps(messages, ensure_ascii=False, default=str))


# ── L1: 裁剪中间消息 ─────────────────────────────

def snip_compact(messages: list[dict], max_messages: int = MAX_MESSAGES) -> list[dict]:
    """消息数超过 max_messages → 保留头部 keep_head + 尾部，中间占位

    配对保护：裁剪边界若落在 assistant(tool_calls) 与其 tool 结果之间，
    则整体移动边界，保证配对消息不被拆散。
    """
    if len(messages) <= max_messages:
        return messages

    system, rest = _split_system(messages)
    if not rest:
        return messages

    keep_head = min(KEEP_HEAD, max_messages // 2)
    keep_tail = max_messages - keep_head
    head_end, tail_start = keep_head, len(rest) - keep_tail

    # 头部边界：head 最后一条是 tool_call → 向后吞并其 tool 结果
    if head_end > 0 and _is_tool_call_message(rest[head_end - 1]):
        while head_end < len(rest) and _is_tool_result_message(rest[head_end]):
            head_end += 1

    # 尾部边界：tail 第一条是 tool 结果且前一条是 tool_call → 从 tool_call 开始
    if (tail_start > 0 and tail_start < len(rest)
            and _is_tool_result_message(rest[tail_start])
            and _is_tool_call_message(rest[tail_start - 1])):
        tail_start -= 1

    if head_end >= tail_start:
        return messages

    snipped = tail_start - head_end
    placeholder = {"role": "user", "content": f"[snipped {snipped} messages from conversation middle]"}
    return system + rest[:head_end] + [placeholder] + rest[tail_start:]


# ── L2: 旧工具结果占位 ───────────────────────────

def micro_compact(
    messages: list[dict],
    keep_recent: int = KEEP_RECENT_TOOL_RESULTS,
) -> list[dict]:
    """保留最近 keep_recent 个 tool 结果，更早的大结果替换为占位符

    只替换内容，不删除消息——tool 消息必须跟在对应 assistant(tool_calls) 后，
    占位不破坏 API 要求的配对结构。
    """
    tool_indices = [i for i, m in enumerate(messages) if _is_tool_result_message(m)]
    if len(tool_indices) <= keep_recent:
        return messages

    for i in tool_indices[:-keep_recent]:
        msg = messages[i]
        if len(msg.get("content", "") or "") > TOOL_RESULT_SNIP_LENGTH:
            msg["content"] = TOOL_RESULT_PLACEHOLDER
    return messages


# ── L4: LLM 摘要 ─────────────────────────────────

class LLMSummaryError(Exception):
    """摘要 LLM 调用失败（上游负责容错）"""


async def _summarize(messages: list[dict]) -> str:
    """调用 LLM 生成摘要（TEXT ONLY，禁止工具调用）

    摘要调用不需要 tools、不需要重试包装——失败时抛出 LLMSummaryError，
    由调用方决定容错策略。
    """
    from app.harness import streaming  # 函数内 import，避免模块循环依赖

    conversation = json.dumps(messages, ensure_ascii=False, default=str)[:SUMMARY_INPUT_LIMIT]
    prompt = SUMMARY_PROMPT + conversation

    parts: list[str] = []
    async for event, data in streaming.deepseek_chat(
        [{"role": "user", "content": prompt}]
    ):
        if event == "token":
            assert isinstance(data, str)
            parts.append(data)
        elif event == "error":
            raise LLMSummaryError(f"摘要调用失败: {data}")
    return "".join(parts).strip() or "(empty summary)"


async def compact_history(messages: list[dict]) -> list[dict]:
    """L4: 全部历史替换为单条摘要（保留 system）

    摘要失败时返回原 messages（跳过压缩），由 reactive_compact 兜底——
    上游 LLM 主调用仍可能成功；若 413 则由 reactive 处理。
    """
    system, rest = _split_system(messages)
    try:
        summary = await _summarize(rest or messages)
    except LLMSummaryError:
        logger.exception("compact_history 摘要失败，跳过压缩")
        return messages

    compacted: list[dict] = [{"role": "user", "content": f"[Compacted]\n\n{summary}"}]
    return system + compacted


async def reactive_compact(messages: list[dict]) -> list[dict]:
    """413 应急压缩：头部摘要 + 保留尾部 keep_tail 条（配对保护）

    比 compact_history 温和：保留最近对话，只压缩更早的部分。
    摘要失败 → 强裁剪兜底（头部仅保留前 3 条），保证 413 一定有压缩动作。
    """
    system, rest = _split_system(messages)
    if not rest:
        return messages

    tail_start = max(0, len(rest) - REACTIVE_KEEP_TAIL)
    # 配对保护：tail 第一条是 tool 结果且前一条是 tool_call → 从 tool_call 开始
    if (tail_start > 0 and tail_start < len(rest)
            and _is_tool_result_message(rest[tail_start])
            and _is_tool_call_message(rest[tail_start - 1])):
        tail_start -= 1

    head, tail = rest[:tail_start], rest[tail_start:]

    try:
        summary = await _summarize(head) if head else ""
    except LLMSummaryError:
        logger.exception("reactive_compact 摘要失败，强裁剪兜底")
        # 兜底：head 只保留前 3 条（含配对保护）
        head_end = min(KEEP_HEAD, len(head))
        if head_end > 0 and _is_tool_call_message(head[head_end - 1]):
            while head_end < len(head) and _is_tool_result_message(head[head_end]):
                head_end += 1
        return system + head[:head_end] + tail

    if summary:
        compacted: list[dict] = [{"role": "user", "content": f"[Reactive compact]\n\n{summary}"}]
        return system + compacted + tail
    return system + head + tail
