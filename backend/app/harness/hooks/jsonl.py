"""ATOF 日志钩子 — 每事件实时写入，支持按 req_id 分组重建轨迹

每个 agent.run() 生成的原始事件（agent/turn/llm/tool × start/end/error）
实时写入 JSONL 文件，一行一个事件。

相比旧版的「agent:end 时一次性写入聚合轨迹」：
  - 实时落盘，中断最多丢一个事件
  - 格式统一，方便离线聚合为 ATIF 轨迹
  - 不影响其他钩子（log、stats）的行为
"""

import json
import logging
import os
from datetime import datetime, timezone

from app.config import settings
from app.harness.hooks import events
from app.harness.hooks.events import on

logger = logging.getLogger(__name__)

_LOG_DIR = "logs/llm-requests"


def _write_line(data: dict) -> None:
    """实时写入一行 ATOF JSONL"""
    path = os.path.join(
        _LOG_DIR,
        f"{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.jsonl",
    )
    os.makedirs(os.path.dirname(path), exist_ok=True)
    line = json.dumps(data, ensure_ascii=False, default=str) + "\n"
    with open(path, "a", encoding="utf-8") as f:
        f.write(line)
        f.flush()


def _ts() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── agent:start ──────────────────────────────────────────────


@on(events.AGENT_START)
async def on_agent_start(data: dict):
    meta = data.get("meta", {})
    _write_line({
        "event": events.AGENT_START,
        "req_id": data["req_id"],
        "ts": _ts(),
        "session_id": meta.get("session_id"),
        "user_message": meta.get("user_message"),
        "model": settings.DEEPSEEK_MODEL,
        "api_url": f"{settings.DEEPSEEK_BASE_URL}/chat/completions",
    })


# ── turn:start ───────────────────────────────────────────────


@on(events.TURN_START)
async def on_turn_start(data: dict):
    _write_line({
        "event": events.TURN_START,
        "req_id": data["req_id"],
        "turn": data["turn"],
        "ts": _ts(),
    })


# ── llm:start ────────────────────────────────────────────────


@on(events.LLM_START)
async def on_llm_start(data: dict):
    _write_line({
        "event": events.LLM_START,
        "req_id": data["req_id"],
        "turn": data["turn"],
        "ts": _ts(),
        "messages_sent": data.get("messages_sent"),
        "tools_sent": data.get("tools_sent"),
    })


# ── llm:end ──────────────────────────────────────────────────


@on(events.LLM_END)
async def on_llm_end(data: dict):
    ev = {
        "event": events.LLM_END,
        "req_id": data["req_id"],
        "turn": data["turn"],
        "ts": _ts(),
        "finish_reason": data.get("finish_reason"),
        "tokens": data.get("tokens"),
        "content": data.get("content", ""),
        "tool_calls": data.get("tool_calls", []),
    }
    usage = data.get("usage")
    if usage:
        ev["usage"] = usage
    err = data.get("error")
    if err:
        ev["error"] = err
    _write_line(ev)


# ── llm:error ──────────────────────────────────────────────────


@on(events.LLM_ERROR)
async def on_llm_error(data: dict):
    ev = {
        "event": events.LLM_ERROR,
        "req_id": data["req_id"],
        "turn": data["turn"],
        "ts": _ts(),
        "error": data.get("error", ""),
    }
    detail = data.get("detail")
    if detail:
        ev["detail"] = detail
    _write_line(ev)


# ── tool:start ────────────────────────────────────────────────


@on(events.TOOL_START)
async def on_tool_start(data: dict):
    _write_line({
        "event": events.TOOL_START,
        "req_id": data["req_id"],
        "turn": data.get("turn"),
        "ts": _ts(),
        "tool_name": data.get("tool_name", ""),
        "tool_call_id": data.get("tool_call_id", ""),
    })


# ── tool:end ──────────────────────────────────────────────────


@on(events.TOOL_END)
async def on_tool_end(data: dict):
    _write_line({
        "event": events.TOOL_END,
        "req_id": data["req_id"],
        "turn": data.get("turn"),
        "ts": _ts(),
        "tool_name": data.get("tool_name", ""),
        "tool_call_id": data.get("tool_call_id", ""),
        "result": data.get("result", ""),
        "status": data.get("status", "ok"),
    })


# ── turn:end ──────────────────────────────────────────────────


@on(events.TURN_END)
async def on_turn_end(data: dict):
    _write_line({
        "event": events.TURN_END,
        "req_id": data["req_id"],
        "turn": data["turn"],
        "ts": _ts(),
    })


# ── agent:end ──────────────────────────────────────────────────


@on(events.AGENT_END)
async def on_agent_end(data: dict):
    _write_line({
        "event": events.AGENT_END,
        "req_id": data["req_id"],
        "ts": _ts(),
        "total_turns": data.get("total_turns", 0),
        "error": data.get("error"),
    })
