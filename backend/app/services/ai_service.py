"""AI 对话 Manager

职责：
- 作为 AIAgent 的工厂，对外暴露一致的 API
- 保持与现有 API（ai.py）和测试的兼容性
"""

from app.harness.agent import AIAgent
from app.schemas.ai import SessionResponse


async def get_or_create_session(db, user_id: int) -> SessionResponse:
    """获取会话"""
    agent = AIAgent(user_id=user_id)
    return await agent.init_session(db)


async def stream_chat(db, user_id: int, session_id: int, message: str):
    """发送消息，SSE 流式返回"""
    agent = AIAgent(user_id=user_id, session_id=session_id)
    async for event, data in agent.run(db, message):
        yield (event, data)
