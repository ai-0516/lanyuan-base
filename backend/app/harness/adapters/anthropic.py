"""AnthropicAdapter — Anthropic 协议（/v1/messages）

DeepSeek anthropic 端点 / 真 Anthropic 均走此协议（TECH_SPEC §5.3）。

Anthropic 硬约束：
1. tool_result 只能出现在 user 消息里，且含 tool_result 的 user 消息
   不能混入其他 block 类型 → 纯文本 user 与 toolResult 消息互不合并
2. tool_result 必须紧跟其 tool_use（转换保持输入顺序即天然满足）
3. 连续 toolResult 合并到同一条 user 消息（Anthropic 合法，参考
   Hermes convert_messages_to_anthropic）
4. 空 content 用 "(no output)" 占位（Anthropic 拒绝空 content）
5. 重复工具名去重（Anthropic 拒绝，参考 Hermes convert_tools_to_anthropic）
"""

import json
import logging
from typing import cast

from app.config import settings
from app.harness.adapters.llm_adapter import LLMAdapter
from app.harness.adapters.messages import (
    AssistantMessage,
    Message,
    SystemMessage,
    ToolResultMessage,
    UserMessage,
    is_text_block,
    is_thinking_block,
    is_tool_call_block,
)
from app.harness.adapters.providers import Protocol

logger = logging.getLogger(__name__)

NO_OUTPUT_PLACEHOLDER = "(no output)"


class AnthropicAdapter(LLMAdapter):
    """Anthropic 协议（/v1/messages）"""

    protocol = Protocol.ANTHROPIC

    # Anthropic API 必填（DeepSeek anthropic 端点同样要求）。
    # 4096 偏保守，长回复/长工具结果可能截断，调大至 DeepSeek 上限附近
    DEFAULT_MAX_TOKENS = 8192

    @property
    def endpoint_path(self) -> str:
        return "/v1/messages"

    def build_headers(self) -> dict:
        # Anthropic 用 x-api-key（非 Bearer），且必须带版本头
        return {
            "x-api-key": settings.LLM_API_KEY,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }

    def is_end(self, data_str: str, data: dict | None) -> bool:
        # Anthropic 无 [DONE] 行，靠 message_stop 事件（data 已解析）
        return bool(data and data.get("type") == "message_stop")

    def has_tool_calls(self, state: dict) -> bool:
        return bool(state.get("tool_uses"))

    def canonical_to_llm(self, messages: list[Message], tools: list[dict] | None = None) -> dict:
        """canonical → Anthropic 请求体内容部分 {"system": str|None, "messages": [...], "tools"?: [...]}"""
        system = None
        result: list[dict] = []
        pending_results: list[dict] = []  # 连续 toolResult 的累积（合并到一条 user 消息）

        def flush_results() -> None:
            """把累积的 tool_result blocks 合并成一条 user 消息"""
            nonlocal pending_results
            if pending_results:
                result.append({"role": "user", "content": pending_results})
                pending_results = []

        for msg in messages:
            role = msg["role"]
            if role == "system":
                m = cast(SystemMessage, msg)
                system = m["content"]
            elif role == "user":
                flush_results()  # tool_result user 消息与文本 user 互不合并
                m = cast(UserMessage, msg)
                content = m["content"]
                if isinstance(content, str):
                    result.append({"role": "user", "content": [{"type": "text", "text": content}]})
                else:
                    blocks = [{"type": "text", "text": b["text"]} for b in content]
                    if blocks:
                        result.append({"role": "user", "content": blocks})
            elif role == "assistant":
                flush_results()
                m = cast(AssistantMessage, msg)
                blocks: list[dict] = []
                for b in m["content"]:
                    if is_text_block(b):
                        if b["text"].strip():  # 空 text block 跳过（Anthropic 拒绝空块）
                            blocks.append({"type": "text", "text": b["text"]})
                    elif is_thinking_block(b):
                        if b["thinking"].strip():
                            blocks.append({"type": "thinking", "thinking": b["thinking"]})
                    elif is_tool_call_block(b):
                        blocks.append({
                            "type": "tool_use",
                            "id": b["id"],
                            "name": b["name"],
                            "input": b["arguments"],
                        })
                if blocks:
                    result.append({"role": "assistant", "content": blocks})
            elif role == "toolResult":
                m = cast(ToolResultMessage, msg)
                tr: dict = {
                    "type": "tool_result",
                    "tool_use_id": m["tool_call_id"],
                    "content": m["content"] or NO_OUTPUT_PLACEHOLDER,
                }
                if m.get("is_error"):
                    tr["is_error"] = True
                pending_results.append(tr)
        flush_results()

        body: dict = {"system": system, "messages": result}
        if tools:
            body["tools"] = self._convert_tools(tools)
        return body

    @staticmethod
    def _convert_tools(tools: list[dict]) -> list[dict]:
        """OpenAI 形状 tools → Anthropic tools（name/description/input_schema），重复名去重"""
        result: list[dict] = []
        seen: set[str] = set()
        for t in tools:
            fn = t.get("function", {})
            name = fn.get("name", "")
            if name in seen:
                logger.warning("重复工具名 %s 已去重（Anthropic 拒绝重复）", name)
                continue
            seen.add(name)
            result.append({
                "name": name,
                "description": fn.get("description", ""),
                "input_schema": fn.get("parameters") or {"type": "object", "properties": {}},
            })
        return result

    def llm_to_canonical(self, event: dict, state: dict) -> list[tuple[str, object]]:
        """增量解析：喂一个 Anthropic SSE 事件 dict → 本次产出的事件列表

        SSE 事件类型：message_start / content_block_start / content_block_delta /
        content_block_stop / message_delta / message_stop / error。
        tool_use 的 input 是 input_json_delta 跨 chunk 累积的 partial_json，
        块结束时 json.loads（参考 pi-ai / Hermes 解析逻辑）。
        """
        events: list[tuple[str, object]] = []
        etype = event.get("type")

        if etype == "message_start":
            state["usage"] = event.get("message", {}).get("usage")
        elif etype == "content_block_start":
            idx = event.get("index", 0)
            block = dict(event.get("content_block", {}) or {})
            block["_input_json"] = ""
            state.setdefault("blocks", {})[idx] = block
        elif etype == "content_block_delta":
            idx = event.get("index", 0)
            delta = event.get("delta", {}) or {}
            dtype = delta.get("type")
            if dtype == "text_delta":
                text = delta.get("text", "")
                if text:
                    state["token_count"] = state.get("token_count", 0) + 1
                    events.append(("token", text))
            elif dtype == "thinking_delta":
                thinking = delta.get("thinking", "")
                if thinking:
                    state.setdefault("thinking_parts", []).append(thinking)
                    events.append(("reasoning_token", thinking))
            elif dtype == "input_json_delta":
                block = state.get("blocks", {}).get(idx, {})
                block["_input_json"] = block.get("_input_json", "") + delta.get("partial_json", "")
        elif etype == "content_block_stop":
            idx = event.get("index", 0)
            block = state.get("blocks", {}).get(idx, {})
            if block.get("type") == "tool_use":
                arguments: dict = {}
                raw = block.get("_input_json", "")
                if raw.strip():
                    try:
                        arguments = json.loads(raw)
                    except json.JSONDecodeError:
                        logger.warning("tool_use input JSON 解析失败，原样保留: %.200s", raw)
                        arguments = {"_raw": raw}
                state.setdefault("tool_uses", []).append({
                    "id": block.get("id", ""),
                    "name": block.get("name", ""),
                    "arguments": arguments,
                })
        elif etype == "message_delta":
            delta = event.get("delta", {}) or {}
            state["stop_reason"] = delta.get("stop_reason") or state.get("stop_reason")
            if event.get("usage"):
                state["usage"] = {**(state.get("usage") or {}), **event["usage"]}
        elif etype == "error":
            err = event.get("error", {}) or {}
            events.append(("error", {
                "code": err.get("type", "api_error"),
                "message": err.get("message", "Anthropic API 流内错误"),
            }))
        return events

    def finalize(self, state: dict) -> list[tuple[str, object]]:
        """流结束收尾：thinking 合并 / tool_use → tool_call / done / usage"""
        events: list[tuple[str, object]] = []

        thinking_parts = state.get("thinking_parts", [])
        if thinking_parts:
            events.append(("reasoning", "".join(thinking_parts)))

        # tool_use → OpenAI 形状的 tool_call 事件（agent.py 消费格式，事件契约不变）
        for tc in state.get("tool_uses", []):
            events.append(("tool_call", {
                "id": tc["id"],
                "type": "function",
                "function": {
                    "name": tc["name"],
                    "arguments": json.dumps(tc["arguments"], ensure_ascii=False),
                },
            }))

        if not state.get("tool_uses") and state.get("token_count", 0) > 0:
            events.append(("done", ""))

        if state.get("usage"):
            events.append(("usage", state["usage"]))
        return events
