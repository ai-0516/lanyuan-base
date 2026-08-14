"""ResponsesAdapter — OpenAI Responses API 协议（/responses）

DeepSeek 官方 2026 年为 Codex 兼容新增的端点（TECH_SPEC §5.4）：
- base_url: https://api.deepseek.com（与 openai 协议同主机，端点 POST /responses）
- 模型: deepseek-v4-flash / deepseek-v4-pro
- 无状态 API：previous_response_id / conversation 不支持，历史全量走 input items

协议关键规则（参考 DeepSeek 官方文档 + hermes-agent codex_responses_adapter /
pi openai-responses 实现）：
- 请求体用顶层 instructions（system 内容）+ input items 数组
- assistant 消息拆成独立 item：reasoning（thinking 明文回传）/ message / function_call
- thinking 明文回传：{"type": "reasoning", "content": [{"type": "reasoning_text", ...}]}
  （DeepSeek 文档：明文 content 归并到相邻 assistant 消息；summary/encrypted_content 不支持）
- toolResult → {"type": "function_call_output", "call_id", "output"}（错误文本在 content，
  协议无 is_error 字段——与 OpenAI 兼容协议一致）
- tools 是扁平结构 {type, name, description, parameters}（非 Chat Completions 的 function 嵌套）
- SSE 流以 response.completed / response.incomplete / response.failed 结束，无 [DONE]
- token 上限字段名是 max_output_tokens（不是 max_tokens）
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

# 流结束事件（DeepSeek 文档：响应正常完成 / 截断 / 失败时的最后一个事件）
_TERMINAL_EVENTS = {"response.completed", "response.incomplete", "response.failed"}


class ResponsesAdapter(LLMAdapter):
    """OpenAI Responses API 协议（/responses）"""

    protocol = Protocol.RESPONSES

    # Responses API 的 token 上限字段是 max_output_tokens（非 max_tokens）
    DEFAULT_MAX_TOKENS = 8192
    max_tokens_field = "max_output_tokens"

    @property
    def endpoint_path(self) -> str:
        return "/responses"

    def build_headers(self) -> dict:
        # 与 openai 协议同认证方式（Bearer），DeepSeek 官方示例即此格式
        return {"Authorization": f"Bearer {settings.LLM_API_KEY}", "Content-Type": "application/json"}

    def is_end(self, data_str: str, data: dict | None) -> bool:
        # Responses 无 [DONE] 行，靠三个结束事件（data 已解析，type 字段判断）
        return bool(data and data.get("type") in _TERMINAL_EVENTS)

    def has_tool_calls(self, state: dict) -> bool:
        return bool(state.get("tool_acc"))

    def canonical_to_llm(self, messages: list[Message], tools: list[dict] | None = None) -> dict:
        """canonical → Responses 请求体内容部分 {"instructions"?: str, "input": [...], "tools"?: [...]}

        assistant 消息按 block 类型拆成独立 input item（reasoning / message / function_call），
        顺序与模型输出自然顺序一致：先思考 → 答复文本 → 工具调用。
        """
        instructions: list[str] = []
        result: list[dict] = []

        for msg in messages:
            role = msg["role"]
            if role == "system":
                m = cast(SystemMessage, msg)
                if m["content"].strip():
                    instructions.append(m["content"])
            elif role == "user":
                m = cast(UserMessage, msg)
                content = m["content"]
                if isinstance(content, str):
                    text = content
                else:
                    text = "".join(b["text"] for b in content)
                if text.strip():
                    result.append({
                        "role": "user",
                        "content": [{"type": "input_text", "text": text}],
                    })
            elif role == "assistant":
                m = cast(AssistantMessage, msg)
                thinking_parts: list[dict] = []
                text_parts: list[str] = []
                tool_parts: list[dict] = []
                for b in m["content"]:
                    if is_thinking_block(b):
                        if b["thinking"].strip():
                            thinking_parts.append({"type": "reasoning_text", "text": b["thinking"]})
                    elif is_text_block(b):
                        if b["text"].strip():
                            text_parts.append(b["text"])
                    elif is_tool_call_block(b):
                        tool_parts.append({
                            "type": "function_call",
                            "call_id": b["id"],
                            "name": b["name"],
                            "arguments": json.dumps(b["arguments"], ensure_ascii=False),
                        })
                # 输出顺序与模型自然输出一致：reasoning → message → function_call
                # （thinking 明文回传，DeepSeek：明文 content 归并到相邻 assistant 消息）
                if thinking_parts:
                    result.append({"type": "reasoning", "content": thinking_parts})
                if text_parts:
                    result.append({
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "".join(text_parts)}],
                    })
                result.extend(tool_parts)
            elif role == "toolResult":
                m = cast(ToolResultMessage, msg)
                result.append({
                    "type": "function_call_output",
                    "call_id": m["tool_call_id"],
                    "output": m["content"],
                })

        body: dict = {"input": result}
        if instructions:
            body["instructions"] = "\n\n".join(instructions)
        if tools:
            body["tools"] = self._convert_tools(tools)
        return body

    @staticmethod
    def _convert_tools(tools: list[dict]) -> list[dict]:
        """OpenAI 形状 tools → Responses 扁平结构（type/name/description/parameters）"""
        result: list[dict] = []
        for t in tools:
            fn = t.get("function", {})
            name = fn.get("name", "")
            if not name:
                continue
            result.append({
                "type": "function",
                "name": name,
                "description": fn.get("description", ""),
                "parameters": fn.get("parameters") or {"type": "object", "properties": {}},
            })
        return result

    def llm_to_canonical(self, event: dict, state: dict) -> list[tuple[str, object]]:
        """增量解析：喂一个 Responses SSE 事件 dict → 本次产出的事件列表

        SSE 事件类型：response.output_item.added / response.output_text.delta /
        response.reasoning_text.delta / response.function_call_arguments.delta /
        response.completed / response.incomplete / response.failed / error。
        function_call 的 arguments 跨 chunk 累积（按 output_index），finalize 统一产出。
        """
        events: list[tuple[str, object]] = []
        etype = event.get("type")

        if etype == "response.output_text.delta":
            delta = event.get("delta", "")
            if delta:
                state["token_count"] = state.get("token_count", 0) + 1
                events.append(("token", delta))
        elif etype == "response.reasoning_text.delta":
            delta = event.get("delta", "")
            if delta:
                state.setdefault("reasoning_parts", []).append(delta)
                events.append(("reasoning_token", delta))
        elif etype == "response.output_item.added":
            # function_call item 开始：记录 call_id/name（arguments 走 delta 累积）
            item = event.get("item", {}) or {}
            if item.get("type") == "function_call":
                idx = event.get("output_index", 0)
                state.setdefault("tool_acc", {})[idx] = {
                    "id": item.get("call_id") or item.get("id") or "",
                    "name": item.get("name", ""),
                    "arguments": "",
                }
        elif etype == "response.function_call_arguments.delta":
            idx = event.get("output_index", 0)
            acc = state.get("tool_acc", {})
            if idx in acc:
                acc[idx]["arguments"] += event.get("delta", "")
        elif etype in _TERMINAL_EVENTS:
            # usage 在结束事件的 response 对象里（streaming.py 结束信号前已喂给本方法）
            resp = event.get("response", {}) or {}
            if resp.get("usage"):
                state["usage"] = resp["usage"]
            if etype == "response.failed":
                err = resp.get("error", {}) or {}
                events.append(("error", {
                    "code": err.get("code", "api_error"),
                    "message": err.get("message", "LLM API 响应失败"),
                }))
        elif etype == "error":
            # SSE error 事件（与 Anthropic 一致的处理）
            err = event.get("error", {}) or {}
            events.append(("error", {
                "code": err.get("code", "api_error"),
                "message": err.get("message", "Responses API 流内错误"),
            }))
        return events

    def finalize(self, state: dict) -> list[tuple[str, object]]:
        """流结束收尾：reasoning 合并 / function_call → tool_call / done / usage"""
        events: list[tuple[str, object]] = []

        reasoning_parts = state.get("reasoning_parts", [])
        if reasoning_parts:
            events.append(("reasoning", "".join(reasoning_parts)))

        # tool_acc → OpenAI 形状的 tool_call 事件（agent.py 消费格式，事件契约不变）
        for tc in state.get("tool_acc", {}).values():
            events.append(("tool_call", {
                "id": tc["id"],
                "type": "function",
                "function": {
                    "name": tc["name"],
                    "arguments": tc["arguments"],
                },
            }))

        if not state.get("tool_acc") and state.get("token_count", 0) > 0:
            events.append(("done", ""))

        if state.get("usage"):
            events.append(("usage", state["usage"]))
        return events
