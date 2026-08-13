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


def _merge_overlapping_hits(
    hits: list[Message], window: int
) -> list[list[Message]]:
    """把窗口重叠的命中合并为连续片段（PR #51 review）

    为什么需要合并：每条命中会带 ±window 条的上下文窗口（context_window）。
    同一会话内相邻的两条命中（消息 id 差 ≤ 2×window）的窗口必然重叠——
    如果各自独立成 result，同一批消息会重复出现在多个 result 里（浪费
    token，且 LLM 看到的内容互相矛盾）。合并后一个片段对应一个 result，
    context_window 取整个区间，天然去重。

    为什么不依赖 hits 的输入顺序：合并的判据是「同会话内 id 邻接」，与
    hits 的排列顺序无关。当前实现靠 order_by(id) 保证有序，但未来升级为
    FTS 相关度排序后 hit 就是无序的（#42）。这里先按会话分组、组内按 id
    排序，合并结果与输入顺序解耦，两种排序下输出一致。

    段序如何保持：片段按「段内首条 hit 在 hits 中的原始下标」升序排列，
    保证输出顺序与用户请求的 sort（newest/oldest/relevance）一致。

    返回：片段列表。每个片段 = 同一会话内窗口重叠的一组命中（组内按 id
    升序，与 hits 原始顺序无关）；片段之间按 anchor 在 hits 中的下标升序。
    """
    # 按会话分组，同时记住每条 hit 在 hits 中的原始下标（最后排序用）
    by_conv: dict[int, list[tuple[int, Message]]] = {}
    for idx, hit in enumerate(hits):
        by_conv.setdefault(hit.conversation_id, []).append((idx, hit))

    # 组内按 id 排序 → 贪心合并：与当前片段末尾同会话且 id 差 ≤ 2×window
    # 说明窗口重叠 → 归入同一片段；否则新开片段。
    segments: list[tuple[int, list[Message]]] = []  # (anchor 原始下标, 片段内命中)
    for conv_hits in by_conv.values():
        conv_hits.sort(key=lambda t: t[1].id)
        for idx, hit in conv_hits:
            can_merge = bool(
                segments  # 已有片段可比较
                and segments[-1][1][-1].conversation_id == hit.conversation_id  # 同会话
                and hit.id - segments[-1][1][-1].id <= 2 * window  # 窗口重叠
            )
            if can_merge:
                segments[-1][1].append(hit)
            else:
                segments.append((idx, [hit]))

    # 片段按 anchor 在 hits 中的原始下标升序 → 保持搜索结果顺序
    return [seg for _, seg in sorted(segments, key=lambda t: t[0])]


def _format_search_history(data: dict) -> str:
    """历史搜索命中 → LLM 摘要（消息内容 + 上下文窗口）"""
    results = data.get("results", [])
    total = data.get("total", 0)
    if not results:
        return "未找到相关历史消息"
    lines = [f"找到 {total} 段相关历史："]
    for r in results:
        lines.append(
            f"—— 命中 #{r.get('message_id')}（{r.get('role')}，此段前后各 "
            f"{r.get('messages_before', 0)}/{r.get('messages_after', 0)} 条）——"
        )
        lines.append(r.get("content", ""))
        for m in r.get("context_window", []):
            if m.get("message_id") != r.get("message_id"):
                lines.append(f"[上下文 {m.get('role')}] {m.get('content', '')}")
    return "\n".join(lines)


@tool(result_formatter=_format_search_history)
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

    # 合并窗口重叠的命中为连续片段（为什么合并、为什么分组排序，
    # 见 _merge_overlapping_hits 的 docstring——PR #51 review）
    segments = _merge_overlapping_hits(hits, window)

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


@router.get("/messages")
async def get_messages(
    before_id: int | None = None,
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user),
):
    """分页获取用户历史消息（TECH_SPEC 8.5 方案 B：默认最新 + 下拉加载）

    游标分页（message id，created_at 可能撞秒不用它做游标），跨 conversation
    按时间直查——前端完全不感知 session 边界（rotation 后旧会话消息同样返回）。
    返回 id 倒序（最新在前），has_more 指示是否还有更早消息。
    """
    limit = max(1, min(limit, 50))
    stmt = (
        select(Message)
        .join(Conversation, Message.conversation_id == Conversation.id)
        .where(Conversation.user_id == user_id)
    )
    if before_id is not None:
        stmt = stmt.where(Message.id < before_id)
    stmt = stmt.order_by(Message.id.desc()).limit(limit + 1)  # 多取 1 判断 has_more
    msgs = (await db.execute(stmt)).scalars().all()
    has_more = len(msgs) > limit
    msgs = msgs[:limit]

    return api_success({
        "messages": [
            {
                "id": m.id,
                "role": m.role,
                "content": m.content,
                "tool_calls": json.loads(m.tool_calls) if m.tool_calls else None,
                "tool_call_id": m.tool_call_id,
                "created_at": m.created_at.isoformat() if m.created_at else "",
            }
            for m in msgs
        ],
        "has_more": has_more,
    })


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
