"""AIAgent — 纯 LLM 交互层

只负责与 LLM 的对话循环，不关心数据库、会话、持久化。
输入 messages（已组装好的 DeepSeek 格式数组），输出事件流。

Agent Loop 逻辑：
  1. 调 LLM（传入 messages + tools）
  2. 如果返回 tool_call → 回调 tool_executor 执行 → 结果回填 messages → 继续
  3. 如果返回纯文本 → done
"""

from app.config import settings
from app.harness import streaming

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

    async def run(self, messages: list[dict], db=None, user_id=None):
        """Agent Loop

        产出 (event, data) 元组：
          ("token", content)   — AI 回复文字
          ("tool_call", dict)  — 模型请求调用工具（前端可用此事件展示状态）
          ("done", "")         — 流正常结束
          ("error", msg)       — 错误提示
        """
        source = streaming.deepseek_chat if settings.DEEPSEEK_API_KEY else streaming.mock_chat

        for turn in range(_MAX_TURNS):
            # 是否需要传 tools？mock 模式下不传
            kw = {}
            if self.tools and settings.DEEPSEEK_API_KEY:
                kw["tools"] = self.tools

            tool_calls = []
            full_reply = ""

            async for event, data in source(messages, **kw):
                if event == "token":
                    full_reply += data
                elif event == "tool_call":
                    tool_calls.append(data)
                yield (event, data)

            # 无工具调用 → 结束
            if not tool_calls:
                return

            # 有工具调用 → 执行 → 回填 → 继续
            for tc in tool_calls:
                if self.tool_executor:
                    result = await self.tool_executor(db, user_id, tc)
                else:
                    result = f"未配置工具执行器，无法执行: {tc['function']['name']}"

                # 回填 assistant tool_call
                messages.append({
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [tc],
                })
                # 回填 tool 结果
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id", ""),
                    "content": result,
                })

        yield ("error", f"Agent 循环超过 {_MAX_TURNS} 次上限")
