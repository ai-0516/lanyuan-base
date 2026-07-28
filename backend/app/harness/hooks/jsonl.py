"""
JSONL 日志钩子 — 记录每次 LLM 调用的完整轮次

从事件中收集数据，在 agent:end 时写入 logs/llm-requests/YYYY-MM-DD.jsonl。
"""

import json
import logging
import os
import secrets
from datetime import datetime, timezone
from typing import Any

from app.config import settings
from app.harness.hooks.events import on

logger = logging.getLogger(__name__)

_LOG_DIR = "logs/llm-requests"

# ── 每轮积累的数据 ──

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
    """追写一行 JSON 到日志文件"""
    if not _entry.get("turns"):
        return
    path = os.path.join(_LOG_DIR, f"{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.jsonl")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    line = json.dumps(_entry, ensure_ascii=False, default=str) + "\n"
    with open(path, "a", encoding="utf-8") as f:
        f.write(line)
        f.flush()


# ── 事件处理器 ──


@on("agent:start")
async def on_agent_start(meta: dict, **_kw):
    """记录元数据，生成请求 ID"""
    _reset()
    _entry["id"] = _gen_req_id()
    _entry["timestamp"] = datetime.now(timezone.utc).isoformat()
    _entry["session_id"] = meta.get("session_id")
    _entry["user_message"] = meta.get("user_message")
    _entry["model"] = settings.DEEPSEEK_MODEL
    _entry["api_url"] = f"{settings.DEEPSEEK_BASE_URL}/chat/completions"
    _entry["turns"] = []


@on("llm:start")
async def on_llm_start(turn: int, messages_sent: list, tools_sent: list | None, **_kw):
    """准备新的一轮"""
    global _current_turn
    _current_turn = {
        "messages_sent": messages_sent,
        "tools_sent": tools_sent,
        "finish_reason": "",
        "tokens": 0,
        "content": "",
        "tool_calls": [],
        "tool_results": [],
    }


@on("llm:end")
async def on_llm_end(turn: int, finish_reason: str, tokens: int, content: str, tool_calls: list, **_kw):
    """记录本轮 LLM 返回"""
    if _current_turn is not None:
        _current_turn["finish_reason"] = finish_reason
        _current_turn["tokens"] = tokens
        _current_turn["content"] = content
        _current_turn["tool_calls"] = tool_calls


@on("tool:end")
async def on_tool_end(tool_name: str, tool_call_id: str, result: str, **_kw):
    """记录工具执行结果到当前轮"""
    if _current_turn is not None:
        _current_turn["tool_results"].append({
            "tool": tool_name,
            "tool_call_id": tool_call_id,
            "result": result,
        })


@on("agent:end")
async def on_agent_end(total_turns: int, error: str | None, **_kw):
    """完成日志条目并写入文件"""
    if _current_turn is not None:
        _entry["turns"].append(_current_turn)
    _entry["duration_ms"] = 0  # 简化：hook 不追踪精确耗时
    if error:
        _entry["error"] = error
    try:
        _write_entry()
    except Exception:
        logger.exception("JSONL 日志写入失败")
