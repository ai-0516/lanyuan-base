"""AI 对话 Manager

职责：
- 编排 AIAgent 的完整调用流程（数据准备 → 执行 → 持久化）
- AIAgent 只负责跟 LLM 交互，不接触 DB
"""

from app.harness import context, session
from app.harness.agent import AIAgent
from app.harness.tools import TOOLS, execute_tool
from app.schemas.ai import MessageItem, SessionResponse


async def get_or_create_session(db, user_id: int) -> SessionResponse:
    """获取会话（后端决定新建或复用）"""
    conv = await session.get_or_create(db, user_id)
    recent = await context.get_recent_messages(db, conv.id)

    return SessionResponse(
        session_id=conv.id,
        title=conv.title or "",
        messages=[
            MessageItem(
                id=m.id, role=m.role,
                content=m.content, created_at=m.created_at,
            )
            for m in recent
        ],
    )


async def stream_chat(db, user_id: int, session_id: int, message: str):
    """发送消息，SSE 流式返回"""
    # ── 1. 归属校验 ──
    conv = await session.verify_ownership(db, session_id, user_id)
    if conv is None:
        yield ("error", "会话不存在或无权限访问")
        return

    # ── 2. 保存用户消息 ──
    await session.save_user_message(db, session_id, message)

    # ── 3. 构建上下文 ──
    history = await context.get_recent_messages(db, session_id)
    deepseek_messages = context.build_deepseek_messages(history, message)

    # ── 4. Agent Loop（含工具调用） ──
    agent = AIAgent(tools=TOOLS, tool_executor=execute_tool)
    full_reply = ""
    try:
        async for event, data in agent.run(
            deepseek_messages,
            db=db,
            user_id=user_id,
            meta={"session_id": session_id, "user_message": message},
        ):
            if event == "token":
                full_reply += data
            yield (event, data)
    except Exception as e:
        error_reply = f"抱歉，AI 回复被中断，请重试。错误：{str(e)}"
        await session.save_assistant_message(db, session_id, error_reply)
        await session.touch_conversation(db, session_id)
        yield ("error", error_reply)
        return

    # ── 5. 持久化 ──
    if full_reply:
        await session.save_assistant_message(db, session_id, full_reply)
        await session.touch_conversation(db, session_id)
