"""
日志钩子 — 记录所有事件的关键信息

每个 event 都记录：session_id, req_id, event_name, 耗时（end event）。
"""

import logging
import time as time_module

from app.config import settings
from app.harness.hooks import events
from app.harness.hooks.events import on

logger = logging.getLogger("app.harness.hooks.log")

# 按 req_id 存储上下文
_session_ids: dict[str, str] = {}
_timestamps: dict[str, dict[str, float]] = {}


def _truncate(text: str, max_len: int = 120) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len] + "..."


def _fmt_elapsed(start: float | None) -> str:
    if start is None:
        return ""
    secs = time_module.time() - start
    return f"{secs:.1f}s"


@on(events.AGENT_START)
async def log_agent_start(data: dict):
    req_id = data["req_id"]
    meta = data.get("meta", {})
    session_id = str(meta.get("session_id", "-"))
    _session_ids[req_id] = session_id
    _timestamps[req_id] = {"agent": time_module.time()}

    msg = meta.get("user_message", "")
    model = settings.DEEPSEEK_MODEL
    logger.info(
        "[%s] [%s] [%s] model=%s 用户: %s",
        session_id, req_id, events.AGENT_START, model, _truncate(msg),
    )


@on(events.TURN_START)
async def log_turn_start(data: dict):
    req_id = data["req_id"]
    turn = data["turn"] + 1
    _timestamps.setdefault(req_id, {})["turn"] = time_module.time()
    sid = _session_ids.get(req_id, "?")
    logger.info("[%s] [%s] [%s] turn=%d", sid, req_id, events.TURN_START, turn)


@on(events.LLM_START)
async def log_llm_start(data: dict):
    req_id = data["req_id"]
    turn = data["turn"] + 1
    messages_cnt = len(data.get("messages_sent", []))
    tools_cnt = len(data.get("tools_sent") or [])
    _timestamps.setdefault(req_id, {})["llm"] = time_module.time()
    sid = _session_ids.get(req_id, "?")
    logger.info(
        "[%s] [%s] [%s] turn=%d messages=%d tools=%d",
        sid, req_id, events.LLM_START, turn, messages_cnt, tools_cnt,
    )


@on(events.LLM_END)
async def log_llm_end(data: dict):
    req_id = data["req_id"]
    turn = data["turn"] + 1
    ts = _timestamps.get(req_id, {})
    elapsed = _fmt_elapsed(ts.pop("llm", None))
    sid = _session_ids.get(req_id, "?")
    reason = data["finish_reason"]
    tokens = data["tokens"]

    if reason == "stop":
        content = data.get("content", "")
        logger.info(
            "[%s] [%s] [%s] turn=%d AI回复(%d tokens): %s (%s)",
            sid, req_id, events.LLM_END, turn, tokens, _truncate(content), elapsed,
        )
    elif reason == "tool_calls":
        tools = data.get("tool_calls", [])
        tool_names = [tc.get("function", {}).get("name", "?") for tc in tools]
        logger.info(
            "[%s] [%s] [%s] turn=%d 调用工具(%s) (%d tokens) (%s)",
            sid, req_id, events.LLM_END, turn, ", ".join(tool_names), tokens, elapsed,
        )
    elif reason == "error":
        error_detail = data.get("error", "未知错误")
        logger.warning(
            "[%s] [%s] [%s] turn=%d LLM 返回错误 (%s): %s",
            sid, req_id, events.LLM_END, turn, elapsed, error_detail,
        )


@on(events.LLM_ERROR)
async def log_llm_error(data: dict):
    """LLM 调用失败时的独立日志"""
    req_id = data["req_id"]
    turn = data["turn"] + 1
    error = data.get("error", "未知错误")
    detail = data.get("detail")
    sid = _session_ids.get(req_id, "?")
    if detail:
        logger.error(
            "[%s] [%s] [%s] turn=%d LLM 请求失败: %s 详情: %s",
            sid, req_id, events.LLM_ERROR, turn, error, detail,
        )
    else:
        logger.error(
            "[%s] [%s] [%s] turn=%d LLM 请求失败: %s",
            sid, req_id, events.LLM_ERROR, turn, error,
        )


@on(events.TOOL_START)
async def log_tool_start(data: dict):
    req_id = data["req_id"]
    tool_name = data.get("tool_name", "?")
    call_id = data.get("tool_call_id", "")[:12]
    tool_args = data.get("tool_args", "")
    # 参数通常很短（如 {"user_id": 6}），但 LLM 生成的 JSON 可能很长（发帖全文）。
    # 超过 200 字符截断 + 标记，完整值在 jsonl 结构化日志（tool_calls 全量）。
    if len(tool_args) > 200:
        tool_args = tool_args[:200] + f"…(truncated len={len(tool_args)}, full in jsonl)"
    _timestamps.setdefault(req_id, {})["tool"] = time_module.time()
    sid = _session_ids.get(req_id, "?")
    logger.info(
        "[%s] [%s] [%s] tool=%s args=%s id=%s",
        sid, req_id, events.TOOL_START, tool_name, tool_args, call_id,
    )


@on(events.TOOL_END)
async def log_tool_end(data: dict):
    req_id = data["req_id"]
    tool_name = data.get("tool_name", "?")
    result = data.get("result", "")
    status = data.get("status", "ok")  # "ok" | "error"
    ts = _timestamps.get(req_id, {})
    elapsed = _fmt_elapsed(ts.pop("tool", None))
    sid = _session_ids.get(req_id, "?")
    logger.info(
        "[%s] [%s] [%s] tool=%s result_len=%d status=%s%s (%s)",
        sid, req_id, events.TOOL_END, tool_name, len(result), status,
        f"\n{result}" if result else "",
        elapsed,
    )


@on(events.TURN_END)
async def log_turn_end(data: dict):
    req_id = data["req_id"]
    turn = data["turn"] + 1
    ts = _timestamps.get(req_id, {})
    elapsed = _fmt_elapsed(ts.pop("turn", None))
    sid = _session_ids.get(req_id, "?")
    logger.info(
        "[%s] [%s] [%s] turn=%d (%s)",
        sid, req_id, events.TURN_END, turn, elapsed,
    )


@on(events.AGENT_END)
async def log_agent_end(data: dict):
    req_id = data["req_id"]
    ts = _timestamps.pop(req_id, {})
    elapsed = _fmt_elapsed(ts.pop("agent", None))
    sid = _session_ids.pop(req_id, "?")
    total_turns = data.get("total_turns", 0)
    error = data.get("error")
    if error:
        logger.warning(
            "[%s] [%s] [%s] turns=%d error=%s (%s)",
            sid, req_id, events.AGENT_END, total_turns, error, elapsed,
        )
    else:
        logger.info(
            "[%s] [%s] [%s] turns=%d ok (%s)",
            sid, req_id, events.AGENT_END, total_turns, elapsed,
        )
