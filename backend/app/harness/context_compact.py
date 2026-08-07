"""上下文压缩管线（适配 learn-claude-code s08 Context Compact）

多层压缩策略，便宜的先跑（0 API），贵的后跑（1 API）：
    L1 snip_message_compact   — 消息数超限 → 裁剪中间，保留头部(前3) + 尾部
    L2 tool_result_compact    — 保留最近 N 组工具结果，更早的占位
    L4 llm_compact            — token 估算超限 → 摘要更早的历史 + 保留尾部（1 API）
    llm_reactive_compact      — 413 应急：保留尾部 + 摘要，摘要失败强裁剪兜底（1 API）

命名约定：带 llm 前缀 = 调用 LLM（花钱）；其余为纯结构操作（0 API）。

设计约定：
- 只操作内存 messages（DeepSeek/OpenAI 兼容格式），不碰 DB
- 压缩管线由 AIAgent 每 turn 调 LLM 前调用；413 由 streaming 层触发 reactive
- system 消息（数组第一条）始终保留，不参与裁剪计数
- assistant(tool_calls) 与其后的 tool 结果消息配对，裁剪/占位不拆散
- llm 系列保留尾部最近消息（含最新 user 消息），只摘要更早的部分——
  交互式对话中用户本轮消息是 LLM 回答的核心，不能被摘要掉
- 阈值来自 settings（生产调优改环境变量，不改代码）
"""

import json
import logging

from app.config import settings
from app.harness.adapters.messages import Message

logger = logging.getLogger(__name__)

# ── 阈值（settings 可配，生产调优无需改代码） ─────
MAX_MESSAGES = settings.COMPACT_MAX_MESSAGES            # L1: 消息数超过则裁剪中间
KEEP_HEAD = settings.COMPACT_KEEP_HEAD                  # L1/L4: 头部保留条数
KEEP_RECENT_TOOL_RESULTS = settings.COMPACT_KEEP_RECENT_TOOL_RESULTS  # L2
TOOL_RESULT_SNIP_LENGTH = settings.COMPACT_TOOL_RESULT_SNIP_LENGTH    # L2
TOOL_RESULT_PLACEHOLDER = "[Earlier tool result compacted. Re-run if needed.]"
COMPACT_THRESHOLD = settings.COMPACT_THRESHOLD          # L4: 字符估算阈值
KEEP_TAIL = settings.COMPACT_KEEP_TAIL                  # llm 系列: 尾部保留条数
SUMMARY_INPUT_LIMIT = settings.COMPACT_SUMMARY_INPUT_LIMIT  # 摘要输入截断

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


# ── 消息格式判断（canonical，TECH_SPEC §4——一次实现，各家通用） ─────

def _is_tool_call_message(msg: Message) -> bool:
    """assistant 消息且 content 含 toolCall block → 工具调用消息"""
    if msg.get("role") != "assistant":
        return False
    content = msg.get("content")
    return isinstance(content, list) and any(b.get("type") == "toolCall" for b in content)


def _is_tool_result_message(msg: Message) -> bool:
    """toolResult 消息 → 工具结果消息"""
    return msg.get("role") == "toolResult"


def _split_system(messages: list[Message]) -> tuple[list[Message], list[Message]]:
    """分离数组头部的 system 消息，返回 (system 段, 其余消息)"""
    if messages and messages[0].get("role") == "system":
        return [messages[0]], list(messages[1:])
    return [], list(messages)


def _compute_tail_start(rest: list[Message], keep_tail: int) -> int:
    """计算尾部起始下标（含配对保护）

    tail 第一条是 tool 结果且前一条是 tool_call → 从 tool_call 开始，
    保证 assistant(tool_calls) 与其 tool 结果消息不拆散。
    """
    tail_start = max(0, len(rest) - keep_tail)
    if (tail_start > 0 and tail_start < len(rest)
            and _is_tool_result_message(rest[tail_start])
            and _is_tool_call_message(rest[tail_start - 1])):
        tail_start -= 1
    return tail_start


def estimate_tokens(messages: list[Message]) -> int:
    """字符数近似估算 token（DeepSeek 无官方 tokenizer，保守偏大）"""
    return len(json.dumps(messages, ensure_ascii=False, default=str))


# ── L1: 消息级别裁剪 ─────────────────────────────

def snip_message_compact(
    messages: list[Message],
    max_messages: int = MAX_MESSAGES,
) -> list[Message]:
    """消息数超过 max_messages → 保留头部 keep_head + 尾部，中间占位

    配对保护：裁剪边界若落在 assistant(tool_calls) 与其 tool 结果之间，
    则整体移动边界，保证配对消息不被拆散。
    """
    if len(messages) <= max_messages:
        return messages

    system, rest = _split_system(messages)
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


# ── L2: 工具结果内容占位 ─────────────────────────

def tool_result_compact(
    messages: list[Message],
    keep_recent: int = KEEP_RECENT_TOOL_RESULTS,
) -> list[Message]:
    """保留最近 keep_recent 个 tool 结果，更早的大结果替换为占位符

    返回新列表（不原地修改传入的 messages，与其他压缩函数风格一致）。
    只替换内容不删消息——tool 消息必须跟在对应 assistant(tool_calls) 后，
    占位不破坏 API 要求的配对结构。
    """
    tool_indices = [i for i, m in enumerate(messages) if _is_tool_result_message(m)]
    if len(tool_indices) <= keep_recent:
        return messages

    result = list(messages)
    for i in tool_indices[:-keep_recent]:
        msg = result[i]
        if len(msg.get("content", "") or "") > TOOL_RESULT_SNIP_LENGTH:
            result[i] = {**msg, "content": TOOL_RESULT_PLACEHOLDER}
    return result


# ── L4 / 应急: LLM 摘要压缩 ──────────────────────

class LLMSummaryError(Exception):
    """摘要 LLM 调用失败（上游负责容错）"""


async def _summarize(messages: list[Message]) -> str:
    """调用 LLM 生成摘要（TEXT ONLY，禁止工具调用）

    摘要调用不需要 tools、不需要重试包装——失败时抛出 LLMSummaryError，
    由调用方决定容错策略。
    """
    from app.harness import streaming  # 函数内 import，避免模块循环依赖

    conversation = json.dumps(messages, ensure_ascii=False, default=str)[:SUMMARY_INPUT_LIMIT]
    prompt = SUMMARY_PROMPT + conversation

    parts: list[str] = []
    async for event, data in streaming.llm_chat(
        [{"role": "user", "content": prompt}]
    ):
        if event == "token":
            assert isinstance(data, str)
            parts.append(data)
        elif event == "error":
            raise LLMSummaryError(f"摘要调用失败: {data}")
    return "".join(parts).strip() or "(empty summary)"


async def _compact_with_summary(
    messages: list[Message],
    marker: str,
    *,
    force_trim_on_failure: bool = False,
) -> list[Message]:
    """公共摘要压缩：保留尾部 KEEP_TAIL 条（含最新 user 消息），对更早历史做摘要

    参数:
        marker: 摘要消息前缀（[Compacted] / [Reactive compact]），便于 debug
        force_trim_on_failure: 摘要失败时的策略
            False → 跳过压缩返回原样（llm_compact：保守，等应急兜底）
            True  → 强裁剪兜底，只保留头部 KEEP_HEAD + 尾部
                    （llm_reactive_compact：413 已发生，必须有压缩动作）
    """
    system, rest = _split_system(messages)

    tail_start = _compute_tail_start(rest, KEEP_TAIL)
    head, tail = rest[:tail_start], rest[tail_start:]
    if not head:
        return messages  # 没有可压缩的早期历史（消息很少时）

    try:
        summary = await _summarize(head)
    except LLMSummaryError:
        logger.exception("%s 摘要失败", marker)
        if not force_trim_on_failure:
            return messages
        # 强裁剪兜底：head 只保留前 KEEP_HEAD 条（含配对保护）
        head_end = min(KEEP_HEAD, len(head))
        if head_end > 0 and _is_tool_call_message(head[head_end - 1]):
            while head_end < len(head) and _is_tool_result_message(head[head_end]):
                head_end += 1
        return system + head[:head_end] + tail

    compacted: list[Message] = [{"role": "user", "content": f"{marker}\n\n{summary}"}]
    return system + compacted + tail


async def llm_compact(messages: list[Message]) -> list[Message]:
    """L4: token 估算超阈值 → 主动压缩（1 API）

    摘要更早的历史 + 保留尾部最近消息。摘要失败 → 跳过压缩返回原样，
    由 413 场景的 llm_reactive_compact 兜底。
    """
    return await _compact_with_summary(messages, "[Compacted]")


async def llm_reactive_compact(messages: list[Message]) -> list[Message]:
    """413 应急压缩（1 API）

    与 llm_compact 的唯一区别：413 已经发生，必须有压缩动作——
    摘要失败时强裁剪兜底（保留头部 KEEP_HEAD + 尾部），而不是跳过。
    """
    return await _compact_with_summary(
        messages, "[Reactive compact]", force_trim_on_failure=True
    )
