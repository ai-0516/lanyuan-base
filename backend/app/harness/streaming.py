"""DeepSeek API 流式客户端 + 模拟回复

职责：
- 模拟回复（无 API Key 时的 fallback）
- DeepSeek API 的 HTTP SSE 请求，支持工具调用
- 逐 token 产出 (event, data) 元组
- LLM 请求/响应日志（用于调试复现）
"""

import json
import logging
import os
import secrets
from datetime import datetime, timezone

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

MOCK_REPLY_TEMPLATE = (
    "收到您的消息：「{message}」\n\n"
    "（当前为模拟模式，未配置 DeepSeek API Key。"
    "请在后端环境变量中设置 DEEPSEEK_API_KEY 以启用真实 AI 对话。）"
)

# ── LLM 请求日志 ──────────────────────────────────

_LOG_DIR = "logs/llm-requests"


def _log_path() -> str:
    """获取今天的 JSONL 日志文件路径"""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return os.path.join(_LOG_DIR, f"{today}.jsonl")


def _gen_req_id() -> str:
    now = datetime.now(timezone.utc)
    ts = now.strftime("%Y%m%d_%H%M%S")
    rand = secrets.token_hex(3)
    return f"req_{ts}_{rand}"


def _write_log_entry(entry: dict):
    """追写一行 JSON 到日志文件（原子操作：一次 write + flush）"""
    path = _log_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    line = json.dumps(entry, ensure_ascii=False, default=str) + "\n"
    with open(path, "a", encoding="utf-8") as f:
        f.write(line)
        f.flush()


def _truncate_str(s: str, max_len: int) -> str:
    """截断长字符串用于日志，保留前后可读性"""
    if len(s) <= max_len:
        return s
    return s[:max_len] + f"...(len={len(s)}, truncated)"


async def mock_chat(messages: list[dict]):
    """模拟回复 — API Key 未配置时使用"""
    user_msg = messages[-1]["content"] if messages else ""
    reply = MOCK_REPLY_TEMPLATE.format(message=user_msg)
    logger.info("LLM request (mock): messages=%d", len(messages))
    yield ("token", reply)
    yield ("done", "")


def _merge_tool_call(
    accumulator: dict[int, dict],
    index: int,
    chunk: dict,
):
    """将流式 chunk 中的 tool_call delta 合并到累加器中"""
    if index not in accumulator:
        accumulator[index] = {
            "id": "",
            "type": "function",
            "function": {"name": "", "arguments": ""},
        }
    tc = chunk.get("tool_calls", [{}])[0]
    if tc.get("id"):
        accumulator[index]["id"] = tc["id"]
    if tc.get("function", {}).get("name"):
        accumulator[index]["function"]["name"] = tc["function"]["name"]
    if tc.get("function", {}).get("arguments"):
        accumulator[index]["function"]["arguments"] += tc["function"]["arguments"]


async def deepseek_chat(
    messages: list[dict],
    tools: list[dict] | None = None,
    *,
    meta: dict | None = None,
):
    """调用 DeepSeek API（SSE 流式），支持工具调用

    产出 (event, data) 元组序列：
    - ("token", content) — AI 回复文字
    - ("tool_call", tool_call_dict) — 模型请求调用工具
    - ("done", "") — 流正常结束（无工具调用时）
    - ("error", msg) — 发生错误
    """
    req_id = _gen_req_id()
    start_time = datetime.now(timezone.utc)
    accumulated_content = ""
    error_msg: str | None = None
    tool_call_accumulator: dict[int, dict] = {}
    finish_reason: str | None = None
    token_count = 0

    try:
        request_body = {
            "model": settings.DEEPSEEK_MODEL,
            "messages": messages,
            "stream": True,
        }
        if tools:
            request_body["tools"] = tools

        logger.info(
            "LLM request: id=%s model=%s messages=%d tools=%s",
            req_id,
            settings.DEEPSEEK_MODEL,
            len(messages),
            "yes" if tools else "no",
        )

        async with httpx.AsyncClient(timeout=60.0) as client:
            async with client.stream(
                "POST",
                f"{settings.DEEPSEEK_BASE_URL}/chat/completions",
                headers={
                    "Authorization": f"Bearer {settings.DEEPSEEK_API_KEY}",
                    "Content-Type": "application/json",
                },
                json=request_body,
            ) as response:
                if response.status_code != 200:
                    error_msg = f"DeepSeek API 返回错误: {response.status_code}"
                    yield ("error", error_msg)
                    return

                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data_str = line[6:].strip()
                    if data_str == "[DONE]":
                        break
                    try:
                        data = json.loads(data_str)
                        choice = data.get("choices", [{}])[0]
                        delta = choice.get("delta", {})
                        finish_reason = choice.get("finish_reason")

                        # 文本 token
                        content = delta.get("content", "")
                        if content:
                            token_count += 1
                            accumulated_content += content
                            yield ("token", content)

                        # 工具调用（按 index 合并多 chunk 参数）
                        if delta.get("tool_calls"):
                            for tc_chunk in delta["tool_calls"]:
                                _merge_tool_call(
                                    tool_call_accumulator,
                                    tc_chunk.get("index", 0),
                                    {"tool_calls": [tc_chunk]},
                                )

                    except json.JSONDecodeError:
                        continue

        # 流结束 — 判断是工具调用还是纯文本
        if tool_call_accumulator:
            logger.info(
                "LLM response: id=%s tokens=%d finish_reason=tool_calls tools=%d",
                req_id,
                token_count,
                len(tool_call_accumulator),
            )
            for tc in tool_call_accumulator.values():
                yield ("tool_call", tc)
        else:
            logger.info(
                "LLM response: id=%s tokens=%d finish_reason=stop",
                req_id,
                token_count,
            )
            yield ("done", "")

    except Exception as e:
        error_msg = str(e)
        logger.error("LLM error: id=%s %s", req_id, error_msg)
        yield ("error", f"AI 对话出错: {error_msg}")

    finally:
        # ── 写日志（finally 确保无论正常/异常都记录） ──
        duration = (datetime.now(timezone.utc) - start_time).total_seconds()
        entry = {
            "id": req_id,
            "timestamp": start_time.isoformat(),
            "duration_ms": round(duration * 1000),
            "session_id": (meta or {}).get("session_id"),
            "user_message": (meta or {}).get("user_message"),
            "messages_sent": [
                {
                    "role": m.get("role"),
                    "content": _truncate_str(m.get("content", ""), 500),
                }
                for m in messages
            ],
            "tools_sent": tools,
            "response": {
                "finish_reason": finish_reason if not error_msg else "error",
                "content": accumulated_content,
                "tool_calls": (
                    list(tool_call_accumulator.values())
                    if tool_call_accumulator
                    else []
                ),
                "tokens": token_count,
            },
            "error": error_msg,
        }
        _write_log_entry(entry)
