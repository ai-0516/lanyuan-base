"""v2 AI 对话 API（WebSocket 事件流，TECH_SPEC §9.1）+ v2 会话统一创建点

与 v1 /api/v1/ai/chat 区分：响应是 DSH 事件白名单子集（§4.2），
不再是 v1 的 token/done/error。

2026-09-04（路线2）：v2 chat 流式统一 WebSocket（/chat/ws，逐帧 JSON
{type, data}）——微信云托管 callContainer 不支持 enableChunked（SSE 通道
在云端不可用），SSE POST /chat 端点已删除。

M3（issue #90）会话演进（§5.1 退役 / §5.3 落地；PR #97 review 定案）：
- **前端先创建 session**：POST /api/v2/ai/session（ai_service.get_or_create_session_v2，
  统一创建点）→ 返回 session_id；对话必须携带（不带 → 拒绝）
- 对话复用：DSH 侧 get-or-load-or-create（内存复用 / 持久化 resume / 兜底 create），
  同 id 续写；session id 纯 uuid（§6.3：不再编码 user_id）
- FastAPI 是身份权威：owner 映射在创建时写入 sessions 表 owner_user_id（§8.2），
  DSH 侧桥插件经内部身份端点 GET /api/v2/internal/sessions/{id}/owner 查 owner（§6.3）
"""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, status, WebSocket

from app.ai.dsh_runtime import dsh_runtime
from app.ai.event_layer import is_done_event, should_forward
from app.ai.history import project_messages
from app.api.deps import get_current_user
from app.core.database import get_db
from app.core.security import decode_access_token
from app.services.ai_service import get_or_create_session_v2, get_session_owner
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai", tags=["AI 对话 v2"])

# WS 首帧等待超时（秒）——协议：连接后 10s 未收到首帧 → close 1008。
# 模块级常量便于测试 monkeypatch（超时分支用例无需真实等 10s）。
WS_FIRST_FRAME_TIMEOUT = 10


async def _chat_events(prompt: str, session_id: str, user_id: int):
    """DSH 事件 → 白名单过滤生成器（传输无关，2026-09-04：SSE 帧 → 事件 dict）

    2b 验证过的队列模式；M3：session 复用，同 id 续写。产出事件结构
    {type, data}（与 event_layer 白名单一致，WS 逐帧 JSON 发送）。
    """
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
                    yield {"type": event.get("type"), "data": event.get("data") or {}}
                    if is_done_event(event):
                        break
            elif n.method == "session.status" and n.payload.get("status") == "idle":
                # done 兜底（§4.3；正常路径 turn/end 已 break）
                break
    except Exception:
        logger.exception("v2 chat 事件流异常")
        yield {"type": "error", "data": {"message": "请重试"}}
    finally:
        if not run_task.done():
            run_task.cancel()
        await asyncio.gather(run_task, return_exceptions=True)


async def _ws_error(websocket: WebSocket, code: int, message: str) -> None:
    """WS 失败路径：先推 error 帧（前端可读文案）再关闭连接"""
    try:
        await websocket.send_text(
            json.dumps({"type": "error", "data": {"message": message}}, ensure_ascii=False)
        )
    except Exception:
        pass
    await websocket.close(code=code)


@router.websocket("/chat/ws")
async def chat_ws(websocket: WebSocket):
    """v2 对话（WebSocket 流式，2026-09-04 路线2：SSE 在微信云托管不可用，
    callContainer 不支持流式 → 统一 WS 单一通道）。

    协议（一轮对话 = 一条连接）：
    1. 连接后首帧 {token, session_id, message}（JWT 放帧内——URL/header
       不携带敏感信息；10s 内未收到 → 1008）
    2. 校验：token 无效 → 4401；session 非本人/不存在 → 4403（fail-closed，
       owner 校验复用 messages 端点同款语义）
    3. 通过后逐帧推送 {type, data}（白名单子集 §4.2，DSH 原样），
       turn/end / 兜底 idle 后关闭（1000）
    4. 任何失败：先推 error 帧（{type:"error", data:{message}}）再 close
    """
    await websocket.accept()
    try:
        first = await asyncio.wait_for(
            websocket.receive_json(), timeout=WS_FIRST_FRAME_TIMEOUT
        )
    except Exception:
        await websocket.close(code=1008)
        return

    token = str(first.get("token") or "").strip()
    message = str(first.get("message") or "").strip()
    session_id = str(first.get("session_id") or "").strip()
    if not message:
        await _ws_error(websocket, 1008, "消息不能为空")
        return
    if not session_id:
        await _ws_error(websocket, 1008, "缺少 session_id")
        return

    payload = decode_access_token(token) if token else None
    # sub 防御（PR #101 第 3 轮 review）：token 有效但 sub 缺失/非数字 → int() 抛
    # ValueError 会导致未捕获直接断连（无 error 帧）——JWT 自签不会发生，兜底
    # 归一为 4401（与 token 无效同语义），保证失败路径永远先推 error 帧再关。
    try:
        user_id = int(payload.get("sub", 0)) if payload else 0
    except (TypeError, ValueError):
        user_id = 0
    if user_id <= 0:
        await _ws_error(websocket, 4401, "登录已过期，请重新登录")
        return

    # 归属校验（PR #97 review：session owner 必须是调用者本人，防横向越权）
    from app.core.database import async_session_factory

    async with async_session_factory() as db:
        owner = await get_session_owner(db, session_id)
    if owner is None or owner != user_id:
        await _ws_error(websocket, 4403, "会话不存在或无权访问")
        return

    try:
        async for evt in _chat_events(message, session_id, user_id):
            await websocket.send_text(json.dumps(evt, ensure_ascii=False))
    except Exception:
        logger.exception("v2 chat WS 推送异常")
        try:
            await _ws_error(websocket, 1011, "请重试")
        except Exception:
            pass
        return
    await websocket.close(code=1000)


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

