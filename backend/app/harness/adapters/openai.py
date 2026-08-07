"""OpenAIAdapter — OpenAI 兼容协议（/chat/completions）

DeepSeek / Qwen / Moonshot / 智谱 均走此协议（TECH_SPEC §5.2）。

关键规则：
- assistant content 必须拼成字符串，不能发 block 数组（pi-ai 教训：
  DeepSeek 等模型会镜像 block 结构产生递归嵌套输出）
- thinking blocks → 顶层 reasoning_content（DeepSeek 扩展）
  **硬约束（DeepSeek V4）**：模型返回 reasoning_content 后后续 turn 必须
  原样回传，否则 HTTP 400 "reasoning_content must be passed back"
  （Hermes #15700/#17212/#17825）
- toolResult → role=tool + tool_call_id（is_error 忽略，错误文本已在 content）
"""

import json
import logging
from typing import cast

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

logger = logging.getLogger(__name__)


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


class OpenAIAdapter(LLMAdapter):
    """OpenAI 兼容协议（/chat/completions）"""

    protocol = "openai"

    @property
    def endpoint_path(self) -> str:
        return "/chat/completions"

    def build_headers(self, api_key: str) -> dict:
        return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    def is_end_signal(self, data_str: str) -> bool:
        return data_str == "[DONE]"

    def is_end_data(self, data: dict) -> bool:
        return False

    def canonical2llm(self, messages: list[Message], tools: list[dict] | None = None) -> dict:
        """canonical → OpenAI 兼容请求体的内容部分 {"messages": [...], "tools"?: [...]}"""
        result: list[dict] = []
        for msg in messages:
            role = msg["role"]
            if role == "system":
                m = cast(SystemMessage, msg)
                result.append({"role": "system", "content": m["content"]})
            elif role == "user":
                m = cast(UserMessage, msg)
                content = m["content"]
                if isinstance(content, str):
                    result.append({"role": "user", "content": content})
                else:
                    text = "".join(b["text"] for b in content)
                    if text:
                        result.append({"role": "user", "content": text})
            elif role == "assistant":
                m = cast(AssistantMessage, msg)
                entry: dict = {"role": "assistant", "content": None}
                text_parts = [b["text"] for b in m["content"] if is_text_block(b)]
                thinking_parts = [b["thinking"] for b in m["content"] if is_thinking_block(b)]
                tool_calls = [b for b in m["content"] if is_tool_call_block(b)]
                if text_parts:
                    entry["content"] = "".join(text_parts)  # 字符串化，不发送 block 数组
                if thinking_parts:
                    # DeepSeek V4 硬约束：reasoning_content 必须原样回传
                    entry["reasoning_content"] = "\n".join(thinking_parts)
                if tool_calls:
                    entry["tool_calls"] = [
                        {
                            "id": tc["id"],
                            "type": "function",
                            "function": {
                                "name": tc["name"],
                                "arguments": json.dumps(tc["arguments"], ensure_ascii=False),
                            },
                        }
                        for tc in tool_calls
                    ]
                result.append(entry)
            elif role == "toolResult":
                m = cast(ToolResultMessage, msg)
                result.append({
                    "role": "tool",
                    "tool_call_id": m["tool_call_id"],
                    "content": m["content"],
                })
        body: dict = {"messages": result}
        if tools:
            body["tools"] = tools
        return body

    def llm2canonical(self, event: dict, state: dict) -> list[tuple[str, object]]:
        """增量解析：喂一个 OpenAI SSE 事件 dict → 本次产出的事件列表

        从 streaming.py deepseek_chat 的解析逻辑原样搬移（行为不变）：
        reasoning_content / content / tool_calls index 合并 / usage。
        """
        events: list[tuple[str, object]] = []
        choice = event.get("choices", [{}])[0]
        delta = choice.get("delta", {})

        # 思考过程 token（DeepSeek 推理模型）
        rc = delta.get("reasoning_content", "")
        if rc:
            state.setdefault("reasoning_parts", []).append(rc)
            events.append(("reasoning_token", rc))

        # 文本 token
        content = delta.get("content", "")
        if content:
            state["token_count"] = state.get("token_count", 0) + 1
            events.append(("token", content))

        # 工具调用（按 index 合并多 chunk 参数）
        if delta.get("tool_calls"):
            acc = state.setdefault("tool_acc", {})
            for tc_chunk in delta["tool_calls"]:
                _merge_tool_call(
                    acc,
                    tc_chunk.get("index", 0),
                    {"tool_calls": [tc_chunk]},
                )

        # 捕获 usage（通常在最后一个 chunk 中）
        if event.get("usage"):
            state["usage"] = event["usage"]
        return events

    def finalize(self, state: dict) -> list[tuple[str, object]]:
        """流结束收尾：reasoning 合并 / tool_call 输出 / done / usage"""
        events: list[tuple[str, object]] = []

        reasoning_parts = state.get("reasoning_parts", [])
        if reasoning_parts:
            events.append(("reasoning", "".join(reasoning_parts)))

        tool_acc = state.get("tool_acc", {})
        if tool_acc:
            for tc in tool_acc.values():
                events.append(("tool_call", tc))
        elif state.get("token_count", 0) > 0:
            events.append(("done", ""))

        if state.get("usage"):
            events.append(("usage", state["usage"]))
        return events
