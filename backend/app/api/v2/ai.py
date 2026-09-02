"""v2 AI 对话 API（SSE 事件流，TECH_SPEC §9.1）+ v2 会话统一创建点

与 v1 /api/v1/ai/chat 区分：响应是 DSH 事件白名单子集（§4.2），
不再是 v1 的 token/done/error。

M3（issue #90）会话演进（§5.1 退役 / §5.3 落地；PR #97 review 定案）：
- **前端先创建 session**：POST /api/v2/ai/session（ai_service.get_or_create_session_v2，
  统一创建点）→ 返回 session_id；对话请求必须携带（不带 → 422）
- 对话复用：DSH 侧 get-or-load-or-create（内存复用 / 持久化 resume / 兜底 create），
  同 id 续写；session id 纯 uuid（§6.3：不再编码 user_id）
- FastAPI 是身份权威：owner 映射在创建时写入 sessions 表 owner_user_id（§8.2），
  DSH 侧桥插件经内部身份端点 GET /api/v2/internal/sessions/{id}/owner 查 owner（§6.3）
- session id 经响应头 `X-Session-Id` 回传（事件流保持 DSH 事件原样，§4.1 不改写）
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.ai.dsh_runtime import dsh_runtime
from app.ai.event_layer import format_sse, is_done_event, should_forward
from app.ai.history import project_messages
from app.api.deps import get_current_user
from app.core.database import get_db
from app.services.ai_service import get_or_create_session_v2, get_session_owner
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai", tags=["AI 对话 v2"])

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}


class ChatRequestV2(BaseModel):
    message: str
    # M3（PR #97 review 定案）：前端先经 POST /api/v2/ai/session 创建 session，
    # 对话请求必须携带 session_id（服务端不再生成）
    session_id: str


async def _stream_chat(prompt: str, session_id: str, user_id: int):
    """事件层过滤 → SSE 帧（2b 验证过的队列模式；M3：session 复用，同 id 续写）"""
    loop = asyncio.get_running_loop()
    q: asyncio.Queue = asyncio.Queue()

    def on_notification(n) -> None:
        loop.call_soon_threadsafe(q.put_nowait, n)

    run_task = asyncio.create_task(
        asyncio.to_thread(dsh_runtime.run, prompt, session_id, on_notification)
    )

    try:
        while True:
            n = await q.get()
            if n.method == "session.event":
                event = n.payload.get("event") or {}
                if should_forward(event):
                    yield format_sse(event)
                    if is_done_event(event):
                        break
            elif n.method == "session.status" and n.payload.get("status") == "idle":
                # done 兜底（§4.3；正常路径 turn/end 已 break）
                break
    except Exception:
        logger.exception("v2 chat SSE 流异常")
        yield "event: error\ndata: {\"message\": \"请重试\"}\n\n"
    finally:
        if not run_task.done():
            run_task.cancel()
        await asyncio.gather(run_task, return_exceptions=True)


@router.post("/session")
async def create_session(
    user_id: int = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """v2 会话统一创建点（PR #97 review 定案）：前端先调用拿 session_id，再发对话。

    语义 = ai_service.get_or_create_session_v2：该用户已有 session 复用最近一条，
    没有则新建（sessions 表 + owner 映射）。不在这里做任何 DSH 侧操作——
    agent 状态由首次对话时 DSH get-or-load-or-create 物化。
    """
    session_id = await get_or_create_session_v2(db, user_id)
    return {"session_id": session_id}


@router.get("/session/{session_id}/messages")
async def session_messages(
    session_id: str,
    before_seq: int | None = None,
    limit: int = 20,
    user_id: int = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """v2 历史列表（TECH_SPEC §10.4：数据源 = DSH session 日志派生，v1 表不读）。

    MySQL events 表（append-only 事件日志）→ 用户视图消息序列
    （app.ai.history.project_messages 投影：user/message → 用户气泡、
    step/start + text-delta → assistant 气泡，纯工具步骤/空回复丢弃）。

    **分页契约（turn 级，同轮不拆分）**：
    - `before_seq`：turn 游标（上次返回的 cursor = 本页最旧 turn/start 的 seq；
      加载更早 = 取 seq < cursor 的 turn）
    - `limit`：每页最多取多少个 turn（默认 20 轮；1 个 turn 产出 1-3 条消息）
    - 返回 `messages` 倒序（最新在前）+ `cursor` + `has_more`
    - **为什么按 turn 分页**：DSH 真实事件序是 turn/start → step/start →
      user/message → chunk → turn/end——assistant 段的起始 seq 小于同轮
      user 消息的 seq，任何「事件 seq / 消息 seq」游标都会把一轮对话拆到
      不同页（回复先于提问）。turn/start..turn/end 是完整边界，按 turn 取
      事件窗口投影，一轮对话必然成对出现。

    归属校验与 /chat 一致（owner 必须是调用者，否则 403——防 session 枚举）。
    """
    limit = max(1, min(limit, 50))
    owner = await get_session_owner(db, session_id)
    if owner is None or owner != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="session 不存在或无权访问",
        )

    # 1. 本页 turn 起点们（cursor 前 limit 个 turn/start；取 limit+1 判断 has_more）
    stmt = (
        "SELECT seq FROM events WHERE session_id = :sid AND type = 'turn/start' "
    )
    params: dict = {"sid": session_id, "limit": limit + 1}
    if before_seq is not None:
        stmt += "AND seq < :cursor "
        params["cursor"] = before_seq
    stmt += "ORDER BY seq DESC LIMIT :limit"
    starts = [row[0] for row in (await db.execute(text(stmt), params)).all()]
    if not starts:
        return {"messages": [], "cursor": before_seq, "has_more": False}

    has_more = len(starts) > limit
    starts = starts[:limit]
    earliest = starts[-1]  # 本页最旧 turn 起点（新 cursor）

    # 2. 取 [earliest, 上界) 的事件（升序投影；上界 = cursor 或最新）
    stmt = (
        "SELECT seq, type, time, data FROM events "
        "WHERE session_id = :sid AND seq >= :earliest "
    )
    params = {"sid": session_id, "earliest": earliest}
    if before_seq is not None:
        stmt += "AND seq < :cursor "
        params["cursor"] = before_seq
    stmt += "ORDER BY seq ASC"
    rows = (await db.execute(text(stmt), params)).mappings().all()

    # 3. 投影（事件升序 → UI 顺序），倒序返回（最新在前，对齐 v1）
    msgs = project_messages([dict(r) for r in rows])
    return {
        "messages": list(reversed(msgs)),
        "cursor": earliest,
        "has_more": has_more,
    }


@router.post("/chat")
async def chat(
    data: ChatRequestV2,
    user_id: int = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not data.message.strip():
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="消息不能为空")

    # M3：session_id 必填（前端先创建，§5.3 统一创建点）；id 即身份（§6.3）
    session_id = data.session_id.strip()
    if not session_id:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="缺少 session_id")

    # M3（PR #97 dev-lead review）：session 归属校验——owner 必须是调用者本人。
    # 前端先经 POST /api/v2/ai/session 创建（owner 映射必然存在）；owner 缺失
    # （绕过统一创建点）或非本人 → 403 拒绝（统一 403，防 session 枚举）。
    # 否则调用者 B 持 A 的 session_id 可 resume A 会话上下文，且工具以 A 身份
    # 执行（get_my_profile/记忆等均为 A 的）——横向越权（PR #94 /mcp 同类修复）。
    owner = await get_session_owner(db, session_id)
    if owner is None or owner != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="session 不存在或无权访问",
        )

    return StreamingResponse(
        _stream_chat(data.message.strip(), session_id, user_id),
        media_type="text/event-stream",
        headers={**SSE_HEADERS, "X-Session-Id": session_id},
    )
