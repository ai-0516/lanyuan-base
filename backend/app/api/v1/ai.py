"""AI 对话 API（SSE 流式）"""

import json
import logging

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy import func, or_, select
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
    每条带前后 window 条上下文）。

    语义说明：返回的 total = **合并后片段数**（segment 数）——同一会话内
    窗口重叠的连续命中合并为一个片段（同会话连续命中只返回一条窗口；
    跨会话命中各自独立），因此 total 可能小于实际命中条数。"""

    # 关键词拆词：空格分隔，任一命中即返回（OR，PR #51 review——AND 容易什么都搜不到）
    keywords = [kw for kw in query.strip().split() if kw]
    if not keywords:
        return {"results": [], "total": 0}

    # LIKE 通配符转义：`%`/`_` 按字面量匹配（review #51 建议 1）。
    # SQLAlchemy 参数化绑定（无注入），但裸通配符会改变语义——搜 `%` 会
    # 匹配所有消息。escape="\\" 使 `\` 后的字符按字面量处理。
    def _escape_like(s: str) -> str:
        return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

    # 当前活跃会话（用户最新）——其内容 agent 上下文已有，排除减少噪音
    latest = (await db.execute(
        select(Conversation)
        .where(Conversation.user_id == user_id)
        .order_by(Conversation.updated_at.desc(), Conversation.id.desc())
        .limit(1)
    )).scalars().first()
    current_conv_id = latest.id if latest else None

    # 命中消息：JOIN conversation 归属过滤 + role 限 user/assistant（tool 不搜，
    # 压缩摘要 = tool 消息自动排除）+ LIKE 多关键词 OR（任一命中，PR #51 review）
    stmt = (
        select(Message)
        .join(Conversation, Message.conversation_id == Conversation.id)
        .where(
            Conversation.user_id == user_id,
            Message.role.in_(["user", "assistant"]),
            Message.content.isnot(None),
        )
    )
    if keywords:
        stmt = stmt.where(or_(*[
            Message.content.like(f"%{_escape_like(kw)}%", escape="\\")
            for kw in keywords
        ]))
    if current_conv_id is not None:
        stmt = stmt.where(Message.conversation_id != current_conv_id)
    stmt = stmt.order_by(
        Message.id.asc() if sort == "oldest" else Message.id.desc()
    ).limit(limit)
    hits = (await db.execute(stmt)).scalars().all()

    # 合并窗口重叠的命中（PR #51 review）：同一会话内相邻 hit 的 ±window
    # 窗口会重叠 → 合并为一个连续片段，避免同一消息出现在多个 result 里。
    # 片段边界 = [首条 hit.id - window, 末条 hit.id + window]，全局顺序保持。
    segments: list[list] = []  # 每个元素 = 同一会话且窗口重叠的一组 hit
    for hit in hits:
        if (segments and segments[-1]
                and hit.conversation_id == segments[-1][-1].conversation_id
                and hit.id - segments[-1][-1].id <= 2 * window):
            segments[-1].append(hit)
        else:
            segments.append([hit])

    results = []
    for seg in segments:
        seg_min, seg_max = seg[0].id - window, seg[-1].id + window
        anchor = seg[0]

        # 片段上下文：合并后的连续消息区间（一条查询替代 N 条，顺带优化 N+1）
        context_msgs = (await db.execute(
            select(Message)
            .where(
                Message.conversation_id == anchor.conversation_id,
                Message.id >= seg_min,
                Message.id <= seg_max,
            )
            .order_by(Message.id.asc())
        )).scalars().all()

        # 片段前后还有多少条（提示可翻页，不限于窗口）
        before_total = await db.scalar(
            select(func.count()).select_from(Message).where(
                Message.conversation_id == anchor.conversation_id, Message.id < seg_min
            )
        )
        after_total = await db.scalar(
            select(func.count()).select_from(Message).where(
                Message.conversation_id == anchor.conversation_id, Message.id > seg_max
            )
        )

        results.append({
            "message_id": anchor.id,
            "role": anchor.role,
            "content": _truncate(anchor.content),
            "created_at": anchor.created_at.isoformat() if anchor.created_at else "",
            "conversation_id": anchor.conversation_id,
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
