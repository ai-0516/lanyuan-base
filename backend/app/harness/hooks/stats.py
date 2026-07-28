"""
统计钩子 — 计算 token 用量和缓存命中率

每轮 agent 结束时打印汇总并写入 llm_usage 表。
"""

import logging
from typing import Any

from app.core.database import async_session_factory
from app.harness.hooks import events
from app.harness.hooks.events import on
from app.models.llm_usage import LlmUsage

logger = logging.getLogger("app.harness.hooks.stats")

_stats: dict[str, dict[str, Any]] = {}


@on(events.AGENT_START)
async def on_agent_start(data: dict):
    req_id = data.get("req_id", "-")
    meta = data.get("meta", {})
    _stats[req_id] = {
        "turns": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "cached_tokens": 0,
        "session_id": meta.get("session_id"),
        "user_id": meta.get("user_id"),
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
        details = usage.get("prompt_tokens_details", {}) or {}
        s["cached_tokens"] += details.get("cached_tokens", 0)


@on(events.AGENT_END)
async def on_agent_end(data: dict):
    req_id = data.get("req_id", "-")
    s = _stats.pop(req_id, None)
    if s is None or s["turns"] == 0:
        return

    pt = s["prompt_tokens"]
    ct = s["completion_tokens"]
    tt = s["total_tokens"]
    cached = s["cached_tokens"]
    cache_rate = int(cached / pt * 100) if pt > 0 and cached > 0 else 0

    # 日志
    if cached > 0 and pt > 0:
        logger.info(
            "[%s] [%s] tokens total=%d prompt=%d cached=%d (%d%%) completion=%d turns=%d",
            req_id, events.AGENT_END, tt, pt, cached, cache_rate, ct, s["turns"],
        )
    else:
        logger.info(
            "[%s] [%s] tokens total=%d prompt=%d completion=%d turns=%d",
            req_id, events.AGENT_END, tt, pt, ct, s["turns"],
        )

    # 写入数据库（不阻塞 consumer）
    try:
        async with async_session_factory() as db:
            db.add(LlmUsage(
                req_id=req_id,
                session_id=s["session_id"],
                user_id=s["user_id"],
                total_tokens=tt,
                prompt_tokens=pt,
                completion_tokens=ct,
                cached_tokens=cached,
                cache_rate=cache_rate,
                turns=s["turns"],
            ))
            await db.commit()
    except Exception:
        logger.exception("llm_usage 写入失败 req_id=%s", req_id)
