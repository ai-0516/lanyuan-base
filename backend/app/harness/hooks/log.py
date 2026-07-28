"""
日志钩子 — 记录 agent loop 和 tool 执行的关键节点

替代 agent.py 和 tool_registry.py 中的硬编码 logger.info / logger.warning。
"""

import logging

from app.harness.hooks.events import on

logger = logging.getLogger(__name__)


@on("agent:start")
async def log_agent_start(turn: int, **_kwargs):
    """每轮 LLM 调用前记录"""
    logger.info("Agent Loop: turn=%d → calling LLM", turn + 1)


@on("agent:turn")
async def log_agent_turn(turn: int, finish_reason: str, tokens: int, tool_calls_count: int, **_kwargs):
    """每轮 LLM 调用结束后记录"""
    if finish_reason == "tool_calls":
        logger.info(
            "Agent Loop: turn=%d done tool_calls=%d tokens=%d",
            turn + 1, tool_calls_count, tokens,
        )
    else:
        logger.info(
            "Agent Loop: turn=%d done finish=%s tokens=%d",
            turn + 1, finish_reason, tokens,
        )


@on("agent:end")
async def log_agent_end(total_turns: int, error: str | None, **_kwargs):
    """循环退出前记录"""
    if error:
        logger.warning("Agent Loop: exceeded %d turns", total_turns)


@on("tool:pre")
async def log_tool_call(tool_name: str, **_kwargs):
    """工具执行前记录"""
    logger.info("Agent Loop: tool=%s executing", tool_name)
