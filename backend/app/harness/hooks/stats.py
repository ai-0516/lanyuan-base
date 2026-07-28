"""
统计钩子 — 计算 token 用量和缓存命中率

每个 agent 运行结束时，打印本轮汇总。
"""

import logging
from typing import Any

from app.harness.hooks import events
from app.harness.hooks.events import on

logger = logging.getLogger("app.harness.hooks.stats")

# req_id → 累计数据
_stats: dict[str, dict[str, Any]] = {}


@on(events.AGENT_START)
async def on_agent_start(data: dict):
    req_id = data.get("req_id", "-")
    _stats[req_id] = {
        "turns": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "cached_tokens": 0,
    }


@on(events.LLM_END)
async def on_llm_end(data: dict):
    req_id = data.get("req_id", "-")
    s = _stats.get(req_id)
    if s is None:
        return

    s["turns"] += 1
    usage = data.get("usage")
    if usage:
        s["prompt_tokens"] += usage.get("prompt_tokens", 0)
        s["completion_tokens"] += usage.get("completion_tokens", 0)
        s["total_tokens"] += usage.get("total_tokens", 0)
        # 缓存命中：不同 API 的字段名不同
        details = usage.get("prompt_tokens_details", {}) or {}
        s["cached_tokens"] += details.get("cached_tokens", 0)


@on(events.AGENT_END)
async def on_agent_end(data: dict):
    req_id = data.get("req_id", "-")
    s = _stats.pop(req_id, None)
    if s is None or s["turns"] == 0:
        return

    if s["cached_tokens"] > 0 and s["prompt_tokens"] > 0:
        cache_rate = s["cached_tokens"] / s["prompt_tokens"] * 100
        logger.info(
            "[%s] [%s] tokens total=%d prompt=%d cached=%d (%.0f%%) completion=%d turns=%d",
            req_id, events.AGENT_END,
            s["total_tokens"], s["prompt_tokens"],
            s["cached_tokens"], cache_rate, s["completion_tokens"], s["turns"],
        )
    else:
        logger.info(
            "[%s] [%s] tokens total=%d prompt=%d completion=%d turns=%d",
            req_id, events.AGENT_END,
            s["total_tokens"], s["prompt_tokens"],
            s["completion_tokens"], s["turns"],
        )
