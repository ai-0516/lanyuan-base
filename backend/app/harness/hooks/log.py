"""
日志钩子 — 记录 Agent 运行的关键决策

输出到标准日志（logs/app.log），信息与 ai_service/streaming 互补：
- agent:start — 用户发了什么
- llm:end   — AI 决定做什么（结束还是调工具）
"""

import logging
from typing import Any

from app.harness.hooks import events
from app.harness.hooks.events import on

logger = logging.getLogger("app.harness.hooks.log")


def _truncate(text: str, max_len: int = 120) -> str:
    """截断长文本"""
    if len(text) <= max_len:
        return text
    return text[:max_len] + "..."


@on(events.AGENT_START)
async def log_agent_start(data: dict):
    event_name = events.AGENT_START
    req_id = data.get("req_id", "-")
    meta = data.get("meta", {})
    msg = meta.get("user_message", "")
    logger.info("[%s] [%s] 用户: %s", req_id, event_name, _truncate(msg))


@on(events.LLM_END)
async def log_llm_end(data: dict):
    event_name = events.LLM_END
    req_id = data.get("req_id", "-")
    reason = data["finish_reason"]
    tokens = data["tokens"]
    turn = data["turn"] + 1

    if reason == "stop":
        content = data.get("content", "")
        logger.info(
            "[%s] [%s] turn=%d AI回复(%d tokens): %s",
            req_id, event_name, turn, tokens, _truncate(content),
        )
    elif reason == "tool_calls":
        tools = data.get("tool_calls", [])
        tool_names = [
            tc.get("function", {}).get("name", "?")
            for tc in tools
        ]
        logger.info(
            "[%s] [%s] turn=%d 调用工具(%s) (%d tokens)",
            req_id, event_name, turn, ", ".join(tool_names), tokens,
        )
    elif reason == "error":
        logger.warning("[%s] [%s] turn=%d LLM 返回错误", req_id, event_name, turn)
