"""AI 对话 API（SSE 流式）"""

import json
import logging

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.api.response import api_success
from app.harness.tool_registry import tool
from app.models.conversation import Conversation, Message
from app.schemas.ai import ChatRequest
from app.services import ai_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai", tags=["AI 对话"])


# ── search_history：agent 搜索过往对话历史（TECH_SPEC 8.6，#47） ──

_MAX_SNIPPET = 4000  # 窗口内单条消息截断长度（防 payload 爆炸，对齐 Hermes）


def _truncate(content: str | None, limit: int = _MAX_SNIPPET) -> str:
    """截断消息内容到 limit 字符"""
    if not content:
        return ""
    return content[:limit] + "…" if len(content) > limit else content


@tool
async def search_history(
    query: str,
    limit: int = 3,
    window: int = 5,
    sort: str = "relevance",
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user),
):
    """搜索用户过往对话历史。当用户提到过去聊过的内容、或需要回忆更早
    对话细节时使用。返回命中的消息及其上下文窗口（最多 limit 条命中，
    每条带前后 window 条上下文）。"""

    # 关键词拆词：空格分隔，全部命中才返回（LIKE 起步，FTS 升级见 #42）
    keywords = [kw for kw in query.strip().split() if kw]
    if not keywords:
        return {"results": [], "total": 0}

    # 当前活跃会话（用户最新）——其内容 agent 上下文已有，排除减少噪音
    latest = (await db.execute(
        select(Conversation)
        .where(Conversation.user_id == user_id)
        .order_by(Conversation.updated_at.desc(), Conversation.id.desc())
        .limit(1)
    )).scalars().first()
    current_conv_id = latest.id if latest else None

    # 命中消息：JOIN conversation 归属过滤 + role 限 user/assistant（tool 不搜，
    # 压缩摘要 = tool 消息自动排除）+ LIKE 多关键词 AND
    stmt = (
        select(Message)
        .join(Conversation, Message.conversation_id == Conversation.id)
        .where(
            Conversation.user_id == user_id,
            Message.role.in_(["user", "assistant"]),
            Message.content.isnot(None),
        )
    )
    for kw in keywords:
        stmt = stmt.where(Message.content.like(f"%{kw}%"))
    if current_conv_id is not None:
        stmt = stmt.where(Message.conversation_id != current_conv_id)
    stmt = stmt.order_by(
        Message.id.asc() if sort == "oldest" else Message.id.desc()
    ).limit(limit)
    hits = (await db.execute(stmt)).scalars().all()

    results = []
    for hit in hits:
        # 上下文窗口：同会话内 ±window 条（按 id 近似时间序）
        before = (await db.execute(
            select(Message)
            .where(Message.conversation_id == hit.conversation_id, Message.id < hit.id)
            .order_by(Message.id.desc()).limit(window)
        )).scalars().all()
        before = list(reversed(before))  # 倒序取最近 window 条 → 还原正序
        after = (await db.execute(
            select(Message)
            .where(Message.conversation_id == hit.conversation_id, Message.id > hit.id)
            .order_by(Message.id.asc()).limit(window)
        )).scalars().all()

        # 命中消息前后还有多少条（提示可翻页，不限于窗口）
        before_total = await db.scalar(
            select(func.count()).select_from(Message).where(
                Message.conversation_id == hit.conversation_id, Message.id < hit.id
            )
        )
        after_total = await db.scalar(
            select(func.count()).select_from(Message).where(
                Message.conversation_id == hit.conversation_id, Message.id > hit.id
            )
        )

        context_msgs = list(before) + [hit] + list(after)
        results.append({
            "message_id": hit.id,
            "role": hit.role,
            "content": _truncate(hit.content),
            "created_at": hit.created_at.isoformat() if hit.created_at else "",
            "conversation_id": hit.conversation_id,
            "messages_before": before_total or 0,
            "messages_after": after_total or 0,
            "context_window": [
                {
                    "message_id": m.id,
                    "role": m.role,
                    "content": _truncate(m.content),
                    "created_at": m.created_at.isoformat() if m.created_at else "",
                }
                for m in context_msgs
            ],
        })

    return {"results": results, "total": len(results)}


@router.post("/session")
async def get_session(
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user),
):
    """获取会话（后端决定新建或复用）"""
    session = await ai_service.get_or_create_session(db, user_id)
    return api_success({
        "session_id": session.session_id,
        "title": session.title,
        "messages": [
            {
                "id": m.id,
                "role": m.role,
                "content": m.content,
                "created_at": m.created_at.isoformat(),
            }
            for m in session.messages
        ],
    })


@router.post("/chat")
async def chat(
    data: ChatRequest,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user),
):
    """发送消息，SSE 流式返回"""

    async def event_stream():
        try:
            async for event, content in ai_service.stream_chat(
                db, user_id, data.session_id, data.message
            ):
                if event in ("token", "done", "error", "cmd_new_session", "message:start"):
                    yield f"event: {event}\ndata: {json.dumps(content, ensure_ascii=False)}\n\n"
        except Exception:
            logger.exception("SSE 流异常: user_id=%s session_id=%s", user_id, data.session_id)
            yield f"event: error\ndata: {json.dumps('AI回复被中断，请重试', ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
