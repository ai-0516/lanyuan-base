"""AIAgent — 纯 LLM 交互层

只负责与 LLM 的对话循环，不关心数据库、会话、持久化。
输入 messages（已组装好的 DeepSeek 格式数组），输出事件流。

Agent Loop 逻辑：
  1. 调 LLM（传入 messages + tools）
  2. 如果返回 tool_call → 回调 tool_executor 执行 → 结果回填 messages → 继续
  3. 如果返回纯文本 → done

错误处理（s11）：
  - LLM 调用使用 retry_deepseek_chat，429/529/timeout 自动退避重试
  - 重试耗尽或不可重试错误 → 降级为模拟回复
  - 重大错误（SSE 断流/解析失败）通过 LLM_ERROR 事件传递
"""

import copy
import logging
import secrets
from datetime import datetime, timezone
from typing import Any

from app.config import settings
from app.harness import streaming
from app.harness.errors import LLMStatus
from app.harness.hooks import events

logger = logging.getLogger(__name__)

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

        # LLM 调用源：有 API Key 时用 retry_deepseek_chat（带重试），否则用 mock
        use_real_llm = bool(settings.DEEPSEEK_API_KEY)

        # agent:start — 整个 Agent 循环开始
        events.emit(events.AGENT_START, {"meta": meta, "req_id": correlation_id})

        for turn in range(_MAX_TURNS):
            events.emit(events.TURN_START, {"turn": turn, "req_id": correlation_id})
            # 记录本轮发送的 messages（深拷贝，避免后续被回填污染）
            turn_messages_sent = copy.deepcopy(messages)
            turn_trace: dict[str, Any] = {
                "messages_sent": turn_messages_sent,
                "tools_sent": (copy.deepcopy(self.tools) if use_real_llm else None),
            }

            kw = {}
            if self.tools and use_real_llm:
                kw["tools"] = self.tools

            # llm:start — 即将调用 LLM
            events.emit(events.LLM_START, {
                "turn": turn, "messages_sent": turn_messages_sent,
                "tools_sent": turn_trace["tools_sent"], "req_id": correlation_id,
            })

            tool_calls = []
            full_reply = ""
            token_count = 0
            has_tool_call = False
            has_error = False
            error_msg = ""
            error_code: str | None = None
            usage_data = None
            self._reasoning_content = ""  # 每轮重置
            used_fallback = False  # 本轮是否降级为模拟回复

            # ── 选择 LLM 源 ──
            source = streaming.retry_deepseek_chat if use_real_llm else streaming.mock_chat

            async for event, data in source(messages, **kw):
                if event == "token":
                    assert isinstance(data, str)
                    full_reply += data
                    token_count += 1
                elif event == "reasoning":
                    assert isinstance(data, str)
                    self._reasoning_content = data
                elif event == "reasoning_token":
                    pass
                elif event == "tool_call":
                    tool_calls.append(data)
                    has_tool_call = True
                elif event == "usage":
                    usage_data = data
                elif event == "error":
                    # 结构化错误数据
                    if isinstance(data, dict):
                        code = data.get("code", LLMStatus.UNEXPECTED)
                        error_code = code.value if isinstance(code, LLMStatus) else str(code)
                        error_msg = data.get("message", str(data))
                    else:
                        error_code = LLMStatus.UNEXPECTED.value
                        error_msg = str(data)

                    has_error = True

                    events.emit(events.LLM_ERROR, {
                        "turn": turn,
                        "error": error_msg,
                        "error_code": error_code,
                        "req_id": correlation_id,
                    })

                elif event == "fallback":
                    used_fallback = True
                    full_reply = data["message"]
                    token_count = len(full_reply)
                    has_error = False
                    has_tool_call = False
                    yield ("token", full_reply)
                    yield ("done", "")
                    break

                yield (event, data)

            # ── finish_reason ──
            if used_fallback:
                turn_trace["finish_reason"] = "fallback"
            elif has_error:
                turn_trace["finish_reason"] = "error"
            elif has_tool_call:
                turn_trace["finish_reason"] = "tool_calls"
            else:
                turn_trace["finish_reason"] = "stop"

            turn_trace["tokens"] = token_count
            turn_trace["content"] = full_reply
            turn_trace["tool_calls"] = [] if used_fallback else copy.deepcopy(tool_calls)
            turn_trace["tool_results"] = []

            # llm:end — LLM 调用完成
            llm_end_data: events.LlmEndData = {
                "turn": turn,
                "finish_reason": turn_trace["finish_reason"],
                "tokens": token_count,
                "content": full_reply,
                "tool_calls": [] if used_fallback else copy.deepcopy(tool_calls),
                "tool_calls_count": 0 if used_fallback else len(tool_calls),
                "req_id": correlation_id,
            }
            if usage_data:
                llm_end_data["usage"] = usage_data
            if has_error and error_msg:
                llm_end_data["error"] = error_msg
                llm_end_data["error_code"] = error_code
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
                tool_call_id = tc.get("id", "")
                events.emit(events.TOOL_START, {
                    "tool_name": tool_name, "tool_call_id": tool_call_id,
                    "turn": turn, "req_id": correlation_id,
                })
                try:
                    if self.tool_executor:
                        result = await self.tool_executor(db, user_id, tc)
                    else:
                        result = f"未配置工具执行器，无法执行: {tool_name}"
                    tool_status = "ok"
                except Exception as e:
                    logger.exception("工具 %s 执行异常", tool_name)
                    result = str(e)
                    tool_status = "error"
                events.emit(events.TOOL_END, {
                    "tool_name": tool_name, "tool_call_id": tool_call_id,
                    "result": result, "status": tool_status,
                    "turn": turn, "req_id": correlation_id,
                })

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
