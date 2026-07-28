"""
JSONL 日志钩子 — 记录每次 LLM 调用的完整轮次
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

_entry: dict[str, Any] = {}
_current_turn: dict[str, Any] | None = None


def _reset():
    global _entry, _current_turn
    _entry = {}
    _current_turn = None


def _gen_req_id() -> str:
    now = datetime.now(timezone.utc)
    ts = now.strftime("%Y%m%d_%H%M%S")
    rand = secrets.token_hex(3)
    return f"req_{ts}_{rand}"


def _write_entry():
    if not _entry.get("turns"):
        return
    path = os.path.join(_LOG_DIR, f"{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.jsonl")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    line = json.dumps(_entry, ensure_ascii=False, default=str) + "\n"
    with open(path, "a", encoding="utf-8") as f:
        f.write(line)
        f.flush()


@on(events.AGENT_START)
async def on_agent_start(data: events.AgentStartData):
    _reset()
    meta = data.get("meta", {})
    _entry["id"] = _gen_req_id()
    _entry["timestamp"] = datetime.now(timezone.utc).isoformat()
    _entry["session_id"] = meta.get("session_id")
    _entry["user_message"] = meta.get("user_message")
    _entry["model"] = settings.DEEPSEEK_MODEL
    _entry["api_url"] = f"{settings.DEEPSEEK_BASE_URL}/chat/completions"
    _entry["turns"] = []


@on(events.LLM_START)
async def on_llm_start(data: events.LlmStartData):
    global _current_turn
    _current_turn = {
        "messages_sent": data.get("messages_sent"),
        "tools_sent": data.get("tools_sent"),
        "finish_reason": "",
        "tokens": 0,
        "content": "",
        "tool_calls": [],
        "tool_results": [],
    }


@on(events.LLM_END)
async def on_llm_end(data: events.LlmEndData):
    if _current_turn is not None:
        _current_turn["finish_reason"] = data["finish_reason"]
        _current_turn["tokens"] = data["tokens"]
        _current_turn["content"] = data.get("content", "")
        _current_turn["tool_calls"] = data.get("tool_calls", [])


@on(events.TOOL_END)
async def on_tool_end(data: events.ToolEndData):
    if _current_turn is not None:
        _current_turn["tool_results"].append({
            "tool": data["tool_name"],
            "tool_call_id": data.get("tool_call_id", ""),
            "result": data.get("result", ""),
        })


@on(events.AGENT_END)
async def on_agent_end(data: events.AgentEndData):
    if _current_turn is not None:
        _entry["turns"].append(_current_turn)
    _entry["duration_ms"] = 0
    if error := data.get("error"):
        _entry["error"] = error
    try:
        _write_entry()
    except Exception:
        logger.exception("JSONL 日志写入失败")
