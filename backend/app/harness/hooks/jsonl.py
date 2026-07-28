"""
JSONL 日志钩子 — 记录每次 LLM 调用的完整轮次（并行安全）

每个 agent.run() 生成唯一的 req_id，所有事件携带此 ID。
钩子通过 req_id 区分不同请求，互不干扰。
"""

import json
import logging
import os
import secrets
from datetime import datetime, timezone
from typing import Any

from app.config import settings
from app.harness.hooks import events
from app.harness.hooks.events import on

logger = logging.getLogger(__name__)

_LOG_DIR = "logs/llm-requests"

# req_id → entry dict，支持并行
_entries: dict[str, dict[str, Any]] = {}
_current_turns: dict[str, dict[str, Any] | None] = {}


def _gen_req_id() -> str:
    now = datetime.now(timezone.utc)
    ts = now.strftime("%Y%m%d_%H%M%S")
    rand = secrets.token_hex(3)
    return f"req_{ts}_{rand}"


def _write_entry(req_id: str):
    entry = _entries.get(req_id)
    if not entry or not entry.get("turns"):
        return
    path = os.path.join(_LOG_DIR, f"{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.jsonl")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    line = json.dumps(entry, ensure_ascii=False, default=str) + "\n"
    with open(path, "a", encoding="utf-8") as f:
        f.write(line)
        f.flush()


@on(events.AGENT_START)
async def on_agent_start(data: dict):
    req_id = data.get("req_id", _gen_req_id())
    meta = data.get("meta", {})
    _entries[req_id] = {
        "id": _gen_req_id(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "session_id": meta.get("session_id"),
        "user_message": meta.get("user_message"),
        "model": settings.DEEPSEEK_MODEL,
        "api_url": f"{settings.DEEPSEEK_BASE_URL}/chat/completions",
        "turns": [],
    }
    _current_turns[req_id] = None


@on(events.LLM_START)
async def on_llm_start(data: dict):
    req_id = data.get("req_id", "")
    _current_turns[req_id] = {
        "messages_sent": data.get("messages_sent"),
        "tools_sent": data.get("tools_sent"),
        "finish_reason": "",
        "tokens": 0,
        "content": "",
        "tool_calls": [],
        "tool_results": [],
    }


@on(events.LLM_END)
async def on_llm_end(data: dict):
    req_id = data.get("req_id", "")
    turn = _current_turns.get(req_id)
    if turn is not None:
        turn["finish_reason"] = data["finish_reason"]
        turn["tokens"] = data["tokens"]
        turn["content"] = data.get("content", "")
        turn["tool_calls"] = data.get("tool_calls", [])


@on(events.TOOL_END)
async def on_tool_end(data: dict):
    req_id = data.get("req_id", "")
    turn = _current_turns.get(req_id)
    if turn is not None:
        turn["tool_results"].append({
            "tool": data["tool_name"],
            "tool_call_id": data.get("tool_call_id", ""),
            "result": data.get("result", ""),
        })


@on(events.AGENT_END)
async def on_agent_end(data: dict):
    req_id = data.get("req_id", "")
    turn = _current_turns.get(req_id)
    entry = _entries.get(req_id)
    if turn is not None and entry is not None:
        entry["turns"].append(turn)
    if entry:
        entry["duration_ms"] = 0
        if error := data.get("error"):
            entry["error"] = error
        try:
            _write_entry(req_id)
        except Exception:
            logger.exception("JSONL 日志写入失败 req_id=%s", req_id)
        finally:
            _entries.pop(req_id, None)
            _current_turns.pop(req_id, None)
