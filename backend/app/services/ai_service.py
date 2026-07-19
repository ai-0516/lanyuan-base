"""AI 对话业务逻辑（SSE 流式）"""

import json

import httpx
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.conversation import Conversation, Message
from app.schemas.ai import MessageItem, SessionResponse


async def get_or_create_session(db: AsyncSession, user_id: int) -> SessionResponse:
    """获取最近一次 active 会话，没有则新建"""
    stmt = (
        select(Conversation)
        .where(Conversation.user_id == user_id)
        .order_by(Conversation.updated_at.desc())
        .limit(1)
    )
    result = await db.execute(stmt)
    session = result.scalar_one_or_none()

    if session is None:
        session = Conversation(user_id=user_id, title="")
        db.add(session)
        await db.flush()

    # 获取最近 20 条消息
    msg_stmt = (
        select(Message)
        .where(Message.conversation_id == session.id)
        .order_by(Message.created_at.asc())
        .limit(20)
    )
    msg_result = await db.execute(msg_stmt)
    messages = [
        MessageItem(
            id=m.id,
            role=m.role,
            content=m.content,
            created_at=m.created_at,
        )
        for m in msg_result.scalars().all()
    ]

    return SessionResponse(
        session_id=session.id,
        title=session.title or "",
        messages=messages,
    )


async def stream_chat(
    db: AsyncSession,
    user_id: int,
    session_id: int,
    message: str,
):
    """
    发送消息给 DeepSeek API，SSE 流式返回。
    生成器产出 (event, data) 元组。
    """
    # 校验 session 归属
    conv_result = await db.execute(
        select(Conversation).where(
            Conversation.id == session_id,
            Conversation.user_id == user_id,
        )
    )
    conversation = conv_result.scalar_one_or_none()
    if not conversation:
        yield ("error", "会话不存在或无权限访问")
        return

    # 保存用户消息
    user_msg = Message(
        conversation_id=session_id,
        role="user",
        content=message,
    )
    db.add(user_msg)
    await db.flush()

    # 获取会话历史（最近 20 条）
    msg_stmt = (
        select(Message)
        .where(Message.conversation_id == session_id)
        .order_by(Message.created_at.asc())
        .limit(20)
    )
    msg_result = await db.execute(msg_stmt)
    history = msg_result.scalars().all()

    # 构建 DeepSeek messages
    deepseek_messages = [
        {"role": "system", "content": "你是兰园社区助手，帮助小区业主解答供暖、停车等小区生活问题。请用温暖亲切的语气回复。"}
    ]
    for m in history:
        deepseek_messages.append({"role": m.role, "content": m.content})

    # 效验 API Key
    if not settings.DEEPSEEK_API_KEY:
        # 无 API key 时返回模拟回复
        mock_reply = f"收到您的消息：「{message}」\n\n（当前为模拟模式，未配置 DeepSeek API Key。请在后端环境变量中设置 DEEPSEEK_API_KEY 以启用真实 AI 对话。）"
        yield ("token", mock_reply)
        yield ("done", "")

        # 保存模拟回复
        assistant_msg = Message(
            conversation_id=session_id,
            role="assistant",
            content=mock_reply,
        )
        db.add(assistant_msg)

        # 更新会话时间
        await db.execute(
            update(Conversation)
            .where(Conversation.id == session_id)
            .values()
        )
        await db.commit()
        return

    # 调 DeepSeek API（SSE 流式）
    full_reply = ""
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            async with client.stream(
                "POST",
                f"{settings.DEEPSEEK_BASE_URL}/chat/completions",
                headers={
                    "Authorization": f"Bearer {settings.DEEPSEEK_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": settings.DEEPSEEK_MODEL,
                    "messages": deepseek_messages,
                    "stream": True,
                },
            ) as response:
                if response.status_code != 200:
                    error_text = await response.aread()
                    yield ("error", f"DeepSeek API 返回错误: {response.status_code}")
                    return

                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data_str = line[6:].strip()
                    if data_str == "[DONE]":
                        break
                    try:
                        data = json.loads(data_str)
                        delta = data.get("choices", [{}])[0].get("delta", {})
                        content = delta.get("content", "")
                        if content:
                            full_reply += content
                            yield ("token", content)
                    except json.JSONDecodeError:
                        continue

        yield ("done", "")

        # 保存完整回复
        assistant_msg = Message(
            conversation_id=session_id,
            role="assistant",
            content=full_reply,
        )
        db.add(assistant_msg)
        await db.commit()

    except Exception as e:
        yield ("error", f"AI 对话出错: {str(e)}")
        # 保存错误信息作为回复
        error_reply = f"抱歉，AI 回复被中断，请重试。错误：{str(e)}"
        assistant_msg = Message(
            conversation_id=session_id,
            role="assistant",
            content=error_reply,
        )
        db.add(assistant_msg)
        await db.commit()
