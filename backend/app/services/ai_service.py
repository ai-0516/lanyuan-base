"""AI 对话 Manager

职责：
- 作为 harness 模块的外观（Facade），对外暴露 get_or_create_session / stream_chat
- 编排 harness 子模块的调用顺序
- 保持与现有 API（ai.py）和测试的兼容性

harness 模块：
  session.py   — 会话创建/查找/复用/归属校验
  context.py   — 上下文窗口（历史消息 + System Prompt 组装）
  message.py   — 消息持久化（用户/AI 消息入库、会话时间刷新）
  streaming.py — DeepSeek API 客户端 + 模拟回复
"""

from app.config import settings
from app.harness import context as ctx, message as msg, session, streaming
from app.models.conversation import Message
from app.schemas.ai import MessageItem, SessionResponse


async def get_or_create_session(db, user_id: int) -> SessionResponse:
    """获取会话（后端决定新建或复用）

    对外 API（POST /ai/session 调用）：
    - 查找最近一次 active 的会话
    - 没有则新建
    - 返回 session_id + 历史消息
    """
    conv = await session.get_or_create(db, user_id)
    recent_messages = await ctx.get_recent_messages(db, conv.id)

    return SessionResponse(
        session_id=conv.id,
        title=conv.title or "",
        messages=[
            MessageItem(
                id=m.id,
                role=m.role,
                content=m.content,
                created_at=m.created_at,
            )
            for m in recent_messages
        ],
    )


async def stream_chat(db, user_id: int, session_id: int, message: str):
    """发送消息，SSE 流式返回

    对外 API（POST /ai/chat 调用）：
    1. 校验 session 归属 → 拒绝跨用户访问
    2. 保存用户消息
    3. 获取历史上下文
    4. 调 DeepSeek（真实 API 或模拟模式）
    5. 保存 AI 回复
    6. 刷新会话时间

    产出 (event, data) 元组，符合 SSE 事件协议：
      ("token", content)  — AI 回复文字块
      ("done", "")        — 流结束
      ("error", msg)      — 错误提示
    """
    # ── Step 1: 归属校验 ──
    conv = await session.verify_ownership(db, session_id, user_id)
    if conv is None:
        yield ("error", "会话不存在或无权限访问")
        return

    # ── Step 2: 保存用户消息 ──
    await msg.save_user_message(db, session_id, message)

    # ── Step 3: 获取历史上下文 ──
    history = await ctx.get_recent_messages(db, session_id)
    deepseek_messages = ctx.build_deepseek_messages(history, message)

    # ── Step 4: 选择数据源 — 真实 API 或模拟模式 ──
    source = streaming.deepseek_chat if settings.DEEPSEEK_API_KEY else streaming.mock_chat

    # ── Step 5: 流式返回 + 持久化 ──
    full_reply = ""
    try:
        async for event, data in source(deepseek_messages):
            if event == "token":
                full_reply += data
            yield (event, data)
    except Exception as e:
        error_reply = f"抱歉，AI 回复被中断，请重试。错误：{str(e)}"
        await msg.save_assistant_message(db, session_id, error_reply)
        await msg.touch_conversation(db, session_id)
        yield ("error", error_reply)
        return

    # ── Step 6: 保存 AI 回复 + 刷新会话 ──
    if full_reply:
        await msg.save_assistant_message(db, session_id, full_reply)
        await msg.touch_conversation(db, session_id)
