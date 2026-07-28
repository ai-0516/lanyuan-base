"""
日志钩子 — 记录 agent loop 和 tool 执行的关键节点
"""

import logging

from app.harness.hooks.events import on

logger = logging.getLogger(__name__)


@on("agent:start")
async def log_agent_start(data: dict):
    """整个 Agent 循环启动"""
    logger.info("Agent Loop: started")


@on("llm:start")
async def log_llm_start(data: dict):
    """每轮 LLM 调用前"""
    logger.info("Agent Loop: turn=%d → calling LLM", data["turn"] + 1)


@on("llm:end")
async def log_llm_end(data: dict):
    """每轮 LLM 调用结束后"""
    turn = data["turn"]
    reason = data["finish_reason"]
    tokens = data["tokens"]
    tc = data["tool_calls_count"]
    if reason == "tool_calls":
        logger.info("Agent Loop: turn=%d done tool_calls=%d tokens=%d", turn + 1, tc, tokens)
    else:
        logger.info("Agent Loop: turn=%d done finish=%s tokens=%d", turn + 1, reason, tokens)


@on("agent:end")
async def log_agent_end(data: dict):
    """循环退出前"""
    if error := data.get("error"):
        logger.warning("Agent Loop: exceeded %d turns", data["total_turns"])


@on("tool:start")
async def log_tool_call(data: dict):
    """工具执行前"""
    logger.info("Agent Loop: tool=%s executing", data["tool_name"])
