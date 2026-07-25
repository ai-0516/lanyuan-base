"""AIAgent — 纯 LLM 交互层

只负责与 LLM 的对话循环，不关心数据库、会话、持久化。
输入 messages（已组装好的 DeepSeek 格式数组），输出事件流。

用法：
    agent = AIAgent()
    async for event, data in agent.run(messages):
        ...
"""

from app.config import settings
from app.harness import streaming


class AIAgent:
    """AI 对话 Agent — 纯 LLM 循环

    不持有任何状态，每次 run 都是独立的。
    """

    async def run(self, messages: list[dict]):
        """Agent Loop

        输入已组装好的 messages（含 system prompt + 历史 + 当前消息），
        产出 (event, data) 元组：
          ("token", content)  — AI 回复文字
          ("done", "")        — 流结束
          ("error", msg)      — 错误提示

        当前无工具调用，Loop 是直线路径（LLM → 返回）。
        后续添加 tool_use 时在此处插判断分支：
          if tool_call → execute → feed back → continue
        """
        source = streaming.deepseek_chat if settings.DEEPSEEK_API_KEY else streaming.mock_chat

        async for event, data in source(messages):
            yield (event, data)
