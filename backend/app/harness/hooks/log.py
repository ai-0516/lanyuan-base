"""
日志钩子 — 记录所有事件的关键信息

每个 event 都有值得记录的内容，至少有时间戳和 req_id。
后续按需补充即可。
"""

import logging
from typing import Any

from app.config import settings
from app.harness.hooks import events
from app.harness.hooks.events import on

logger = logging.getLogger("app.harness.hooks.log")


def _truncate(text: str, max_len: int = 120) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len] + "..."


@on(events.AGENT_START)
async def log_agent_start(data: dict):
    req_id = data.get("req_id", "-")
    meta = data.get("meta", {})
    msg = meta.get("user_message", "")
    model = settings.DEEPSEEK_MODEL
    logger.info(
        "[%s] [%s] model=%s 用户: %s",
        req_id, events.AGENT_START, model, _truncate(msg),
    )


@on(events.TURN_START)
async def log_turn_start(data: dict):
    req_id = data.get("req_id", "-")
    turn = data.get("turn", 0) + 1
    logger.info("[%s] [%s] turn=%d", req_id, events.TURN_START, turn)


@on(events.LLM_START)
async def log_llm_start(data: dict):
    req_id = data.get("req_id", "-")
    turn = data.get("turn", 0) + 1
    messages_cnt = len(data.get("messages_sent", []))
    tools_cnt = len(data.get("tools_sent") or [])
    logger.info(
        "[%s] [%s] turn=%d messages=%d tools=%d",
        req_id, events.LLM_START, turn, messages_cnt, tools_cnt,
    )


@on(events.LLM_END)
async def log_llm_end(data: dict):
    req_id = data.get("req_id", "-")
    reason = data["finish_reason"]
    tokens = data["tokens"]
    turn = data["turn"] + 1

    if reason == "stop":
        content = data.get("content", "")
        logger.info(
            "[%s] [%s] turn=%d AI回复(%d tokens): %s",
            req_id, events.LLM_END, turn, tokens, _truncate(content),
        )
    elif reason == "tool_calls":
        tools = data.get("tool_calls", [])
        tool_names = [
            tc.get("function", {}).get("name", "?")
            for tc in tools
        ]
        logger.info(
            "[%s] [%s] turn=%d 调用工具(%s) (%d tokens)",
            req_id, events.LLM_END, turn, ", ".join(tool_names), tokens,
        )
    elif reason == "error":
        logger.warning("[%s] [%s] turn=%d LLM 返回错误", req_id, events.LLM_END, turn)


@on(events.TOOL_START)
async def log_tool_start(data: dict):
    req_id = data.get("req_id", "-")
    tool_name = data.get("tool_name", "?")
    call_id = data.get("tool_call_id", "")[:12]
    logger.info(
        "[%s] [%s] tool=%s id=%s",
        req_id, events.TOOL_START, tool_name, call_id,
    )


@on(events.TOOL_END)
async def log_tool_end(data: dict):
    req_id = data.get("req_id", "-")
    tool_name = data.get("tool_name", "?")
    result = data.get("result", "")
    status = "ok" if result and "error" not in result.lower()[:100] else "err"
    logger.info(
        "[%s] [%s] tool=%s result_len=%d %s",
        req_id, events.TOOL_END, tool_name, len(result), status,
    )


@on(events.TURN_END)
async def log_turn_end(data: dict):
    req_id = data.get("req_id", "-")
    turn = data.get("turn", 0) + 1
    logger.info("[%s] [%s] turn=%d", req_id, events.TURN_END, turn)


@on(events.AGENT_END)
async def log_agent_end(data: dict):
    req_id = data.get("req_id", "-")
    total_turns = data.get("total_turns", 0)
    error = data.get("error")
    if error:
        logger.warning("[%s] [%s] turns=%d error=%s", req_id, events.AGENT_END, total_turns, error)
    else:
        logger.info("[%s] [%s] turns=%d ok", req_id, events.AGENT_END, total_turns)
