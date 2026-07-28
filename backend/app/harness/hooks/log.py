"""
日志钩子 — 记录 agent loop 和 tool 执行的关键节点
"""

import logging

from app.harness.hooks import events
from app.harness.hooks.events import on

logger = logging.getLogger(__name__)


@on(events.AGENT_START)
async def log_agent_start(data: events.AgentStartData):
    logger.info("Agent Loop: started")


@on(events.LLM_START)
async def log_llm_start(data: events.LlmStartData):
    logger.info("Agent Loop: turn=%d → calling LLM", data["turn"] + 1)


@on(events.LLM_END)
async def log_llm_end(data: events.LlmEndData):
    turn = data["turn"]
    reason = data["finish_reason"]
    tokens = data["tokens"]
    tc = data["tool_calls_count"]
    if reason == "tool_calls":
        logger.info("Agent Loop: turn=%d done tool_calls=%d tokens=%d", turn + 1, tc, tokens)
    else:
        logger.info("Agent Loop: turn=%d done finish=%s tokens=%d", turn + 1, reason, tokens)


@on(events.AGENT_END)
async def log_agent_end(data: events.AgentEndData):
    if error := data.get("error"):
        logger.warning("Agent Loop: exceeded %d turns", data["total_turns"])


@on(events.TOOL_START)
async def log_tool_call(data: events.ToolStartData):
    logger.info("Agent Loop: tool=%s executing", data["tool_name"])
