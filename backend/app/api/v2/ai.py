"""v2 AI 对话 API：POST /api/v2/ai/chat（SSE 事件流，TECH_SPEC §9.1）

与 v1 /api/v1/ai/chat 区分：响应是 DSH 事件白名单子集（§4.2），
不再是 v1 的 token/done/error。

M3（issue #90）会话演进（§5.1 退役 / §5.3 落地）：
- 过渡期「每请求新 session（uuid，无注入）」退役 → 正常对话复用 session：
  - 请求带 session_id → 复用/恢复（DSH 侧 get-or-load-or-create）
  - 不带 → 生成 `v2-{纯 uuid}`（§6.3：不再编码 user_id）
- session id 经响应头 `X-Session-Id` 回传（前端下轮携带；事件流保持
  DSH 事件原样，§4.1 不做事件改写）
- FastAPI 是身份权威：record_session_owner 写 sessions 表 owner 映射
  （§8.2），DSH 侧桥插件经内部身份端点查 owner（§6.3）
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.ai.dsh_runtime import dsh_runtime
from app.ai.event_layer import format_sse, is_done_event, should_forward
from app.ai.session_service import new_session_id, record_session_owner
from app.api.deps import get_current_user
from app.core.database import get_db
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai", tags=["AI 对话 v2"])

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}


class ChatRequestV2(BaseModel):
    message: str
    # M3：会话复用。带 session_id → DSH 侧 get-or-load-or-create（内存复用 /
    # 持久化恢复 / 新建）；不带 → 服务端生成（响应头 X-Session-Id 回传）
    session_id: str | None = None


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


@router.post("/chat")
async def chat(
    data: ChatRequestV2,
    user_id: int = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not data.message.strip():
        from fastapi import HTTPException, status

        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="消息不能为空")

    # M3：会话 id = 纯 uuid（§6.3），复用则沿用请求带的（id 即身份，§5.3）
    session_id = (data.session_id or "").strip() or new_session_id()
    # FastAPI 身份权威：owner 映射写入 sessions 表（幂等，同 session 多轮复用）
    await record_session_owner(db, session_id, user_id)

    return StreamingResponse(
        _stream_chat(data.message.strip(), session_id, user_id),
        media_type="text/event-stream",
        headers={**SSE_HEADERS, "X-Session-Id": session_id},
    )
