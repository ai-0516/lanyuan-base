"""AIAgent — 纯 LLM 交互层

只负责与 LLM 的对话循环，不关心数据库、会话、持久化。
输入 messages（已组装好的 DeepSeek 格式数组），输出事件流。

Agent Loop 逻辑：
  1. 调 LLM（传入 messages + tools）
  2. 如果返回 tool_call → 回调 tool_executor 执行 → 结果回填 messages → 继续
  3. 如果返回纯文本 → done
"""

import copy
import json
import os
import secrets
from datetime import datetime, timezone

from app.config import settings
from app.harness import streaming
from app.harness.hooks.events import emit

_MAX_TURNS = 10

# ── LLM 请求日志 ──────────────────────────────────

_LOG_DIR = "logs/llm-requests"


def _gen_req_id() -> str:
    now = datetime.now(timezone.utc)
    ts = now.strftime("%Y%m%d_%H%M%S")
    rand = secrets.token_hex(3)
    return f"req_{ts}_{rand}"


def _write_log_entry(entry: dict):
    """追写一行 JSON 到日志文件"""
    path = os.path.join(_LOG_DIR, f"{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.jsonl")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    line = json.dumps(entry, ensure_ascii=False, default=str) + "\n"
    with open(path, "a", encoding="utf-8") as f:
        f.write(line)
        f.flush()


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
        self._log_turns: list[dict] = []
        self._log_meta: dict = {}
        self._log_req_id: str = ""
        self._log_start: datetime | None = None
        self._error: str | None = None

    def get_log(self) -> dict:
        """返回本次 run 的完整请求日志"""
        if self._log_start is None:
            return {}
        duration = (datetime.now(timezone.utc) - self._log_start).total_seconds()
        return {
            "id": self._log_req_id,
            "timestamp": self._log_start.isoformat(),
            "duration_ms": round(duration * 1000),
            "session_id": self._log_meta.get("session_id"),
            "user_message": self._log_meta.get("user_message"),
            "model": settings.DEEPSEEK_MODEL,
            "api_url": f"{settings.DEEPSEEK_BASE_URL}/chat/completions",
            "turns": self._log_turns,
            "error": self._error,
        }

    async def run(self, messages: list[dict], db=None, user_id=None, meta=None):
        """Agent Loop

        产出 (event, data) 元组：
          ("token", content)   — AI 回复文字
          ("tool_call", dict)  — 模型请求调用工具（前端可用此事件展示状态）
          ("done", "")         — 流正常结束
          ("error", msg)       — 错误提示
        """
        self._log_meta = meta or {}
        self._log_req_id = _gen_req_id()
        self._log_start = datetime.now(timezone.utc)
        self._log_turns = []
        self._error = None

        source = streaming.deepseek_chat if settings.DEEPSEEK_API_KEY else streaming.mock_chat

        # agent:start — 整个 Agent 循环开始
        await emit("agent:start")

        for turn in range(_MAX_TURNS):
            # 记录本轮发送的 messages（深拷贝，避免后续被回填污染）
            turn_messages_sent = copy.deepcopy(messages)
            turn_log: dict = {
                "messages_sent": turn_messages_sent,
                "tools_sent": (copy.deepcopy(self.tools) if settings.DEEPSEEK_API_KEY else None),
            }

            kw = {}
            if self.tools and settings.DEEPSEEK_API_KEY:
                kw["tools"] = self.tools

            # llm:start — 即将调用 LLM
            await emit("llm:start", turn=turn)

            tool_calls = []
            full_reply = ""
            token_count = 0
            has_tool_call = False
            has_error = False
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
                elif event == "error":
                    has_error = True
                yield (event, data)

            # 记录本轮响应
            if has_error:
                turn_log["finish_reason"] = "error"
            elif has_tool_call:
                turn_log["finish_reason"] = "tool_calls"
            else:
                turn_log["finish_reason"] = "stop"
            turn_log["tokens"] = token_count
            turn_log["content"] = full_reply
            turn_log["tool_calls"] = copy.deepcopy(tool_calls)
            turn_log["tool_results"] = []

            # turn:end — 本轮结束
            await emit(
                "turn:end",
                turn=turn,
                finish_reason=turn_log["finish_reason"],
                tokens=token_count,
                tool_calls_count=len(tool_calls),
            )

            # 无工具调用 → 结束
            if not tool_calls:
                turn_log["tool_results"] = []
                self._log_turns.append(turn_log)
                await emit("agent:end", total_turns=turn + 1, error=None)
                return

            # 有工具调用 → 执行 → 回填 → 继续
            for tc in tool_calls:
                tool_name = tc.get("function", {}).get("name", "?")
                if self.tool_executor:
                    result = await self.tool_executor(db, user_id, tc)
                else:
                    result = f"未配置工具执行器，无法执行: {tc['function']['name']}"

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
                turn_log["tool_results"].append({
                    "tool": tool_name,
                    "tool_call_id": tc.get("id", ""),
                    "result": result,
                })

            self._log_turns.append(turn_log)

        self._error = f"Agent 循环超过 {_MAX_TURNS} 次上限"
        yield ("error", self._error)
        await emit("agent:end", total_turns=_MAX_TURNS, error=self._error)
