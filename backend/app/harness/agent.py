"""AIAgent — 纯 LLM 交互层

只负责与 LLM 的对话循环，不关心数据库、会话、持久化。
输入 messages（已组装好的 DeepSeek 格式数组），输出事件流。

Agent Loop 逻辑：
  1. 调 LLM（传入 messages + tools）
  2. 如果返回 tool_call → 回调 tool_executor 执行 → 结果回填 messages → 继续
  3. 如果返回纯文本 → done
"""

import copy
import secrets
from datetime import datetime, timezone
from typing import Any

from app.config import settings
from app.harness import streaming
from app.harness.hooks import events

_MAX_TURNS = 10


class AIAgent:
    """AI 对话 Agent — 纯 LLM 循环

    参数：
        tools: tool definitions 列表（传给 LLM）
        tool_executor: 可选的异步回调，接收 (tool_call) → 返回结果字符串
    """

    def __init__(self, tools: list[dict] | None = None, tool_executor=None):
        self.tools = tools
        self.tool_executor = tool_executor
        self._reasoning_content: str = ""
        self._turns: list[dict] = []
        self._start_time: datetime | None = None

    def get_log(self) -> dict:
        """返回本轮运行记录（用于持久化）"""
        if self._start_time is None:
            return {"turns": []}
        duration = (datetime.now(timezone.utc) - self._start_time).total_seconds()
        return {
            "duration_ms": round(duration * 1000),
            "turns": self._turns,
        }

    async def run(self, messages: list[dict], db=None, user_id=None, meta=None):
        """Agent Loop

        产出 (event, data) 元组：
          ("token", content)   — AI 回复文字
          ("tool_call", dict)  — 模型请求调用工具（前端可用此事件展示状态）
          ("done", "")         — 流正常结束
          ("error", msg)       — 错误提示
        """
        meta = meta or {}
        self._start_time = datetime.now(timezone.utc)
        self._turns = []
        correlation_id = secrets.token_hex(4)

        source = streaming.deepseek_chat if settings.DEEPSEEK_API_KEY else streaming.mock_chat

        # agent:start — 整个 Agent 循环开始
        events.emit(events.AGENT_START, {"meta": meta, "req_id": correlation_id})

        for turn in range(_MAX_TURNS):
            events.emit(events.TURN_START, {"turn": turn, "req_id": correlation_id})
            # 记录本轮发送的 messages（深拷贝，避免后续被回填污染）
            turn_messages_sent = copy.deepcopy(messages)
            turn_trace: dict[str, Any] = {
                "messages_sent": turn_messages_sent,
                "tools_sent": (copy.deepcopy(self.tools) if settings.DEEPSEEK_API_KEY else None),
            }

            kw = {}
            if self.tools and settings.DEEPSEEK_API_KEY:
                kw["tools"] = self.tools

            # llm:start — 即将调用 LLM
            events.emit(events.LLM_START, {"turn": turn, "messages_sent": turn_messages_sent, "tools_sent": turn_trace["tools_sent"], "req_id": correlation_id})

            tool_calls = []
            full_reply = ""
            token_count = 0
            has_tool_call = False
            has_error = False
            error_msg = ""
            usage_data = None
            self._reasoning_content = ""  # 每轮重置

            async for event, data in source(messages, **kw):
                if event == "token":
                    full_reply += data
                    token_count += 1
                elif event == "reasoning":
                    self._reasoning_content = data
                elif event == "reasoning_token":
                    pass  # 前端若展示思考过程可从这里 yield，当前仅取完整文本
                elif event == "tool_call":
                    tool_calls.append(data)
                    has_tool_call = True
                elif event == "usage":
                    usage_data = data
                elif event == "error":
                    has_error = True
                    error_msg = str(data)
                yield (event, data)

            # 记录本轮响应
            if has_error:
                turn_trace["finish_reason"] = "error"
            elif has_tool_call:
                turn_trace["finish_reason"] = "tool_calls"
            else:
                turn_trace["finish_reason"] = "stop"
            turn_trace["tokens"] = token_count
            turn_trace["content"] = full_reply
            turn_trace["tool_calls"] = copy.deepcopy(tool_calls)
            turn_trace["tool_results"] = []

            # llm:end — LLM 调用完成
            llm_end_data: events.LlmEndData = {
                "turn": turn,
                "finish_reason": turn_trace["finish_reason"],
                "tokens": token_count,
                "content": full_reply,
                "tool_calls": copy.deepcopy(tool_calls),
                "tool_calls_count": len(tool_calls),
                "req_id": correlation_id,
            }
            if usage_data:
                llm_end_data["usage"] = usage_data
            if has_error and error_msg:
                llm_end_data["error"] = error_msg
            events.emit(events.LLM_END, llm_end_data)

            # 无工具调用 → 结束
            if not tool_calls:
                turn_trace["tool_results"] = []
                self._turns.append(turn_trace)
                if has_error:
                    yield ("done", {"finish_reason": "error", "error": error_msg})
                events.emit(events.TURN_END, {"turn": turn, "req_id": correlation_id})
                events.emit(events.AGENT_END, {"total_turns": turn + 1, "error": None, "req_id": correlation_id})
                return

            # 有工具调用 → 执行 → 回填 → 继续
            for tc in tool_calls:
                tool_name = tc.get("function", {}).get("name", "?")
                events.emit(events.TOOL_START, {"tool_name": tool_name, "tool_call_id": tc.get("id", ""), "req_id": correlation_id})
                if self.tool_executor:
                    result = await self.tool_executor(db, user_id, tc)
                else:
                    result = f"未配置工具执行器，无法执行: {tc['function']['name']}"
                events.emit(events.TOOL_END, {"tool_name": tool_name, "tool_call_id": tc.get("id", ""), "result": result, "req_id": correlation_id})

                # 回填 assistant tool_call（含 reasoning_content，DeepSeek 推理模型要求）
                msg: dict = {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [tc],
                }
                if self._reasoning_content:
                    msg["reasoning_content"] = self._reasoning_content
                messages.append(msg)
                # 回填 tool 结果
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id", ""),
                    "content": result,
                })
                turn_trace["tool_results"].append({
                    "tool": tool_name,
                    "tool_call_id": tc.get("id", ""),
                    "result": result,
                })

            self._turns.append(turn_trace)
            events.emit(events.TURN_END, {"turn": turn, "req_id": correlation_id})

        error = f"Agent 循环超过 {_MAX_TURNS} 次上限"
        yield ("error", error)
        events.emit(events.AGENT_END, {"total_turns": _MAX_TURNS, "error": error, "req_id": correlation_id})
