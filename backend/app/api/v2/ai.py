"""v2 AI 对话 API：POST /api/v2/ai/chat（SSE 事件流，TECH_SPEC §9.1）

与 v1 /api/v1/ai/chat 区分：响应是 DSH 事件白名单子集（§4.2），
不再是 v1 的 token/done/error。过渡期每请求新 session（uuid，无注入，§5.1）。
"""

from __future__ import annotations

import asyncio
import logging
import uuid

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.ai.dsh_runtime import dsh_runtime
from app.ai.event_layer import format_sse, is_done_event, should_forward
from app.api.deps import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai", tags=["AI 对话 v2"])

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}


class ChatRequestV2(BaseModel):
    message: str


async def _stream_chat(prompt: str):
    """事件层过滤 → SSE 帧（2b 验证过的队列模式）"""
    loop = asyncio.get_running_loop()
    q: asyncio.Queue = asyncio.Queue()
    session_id = f"v2-{uuid.uuid4()}"  # 过渡期每请求新 session（§5.1）

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
):
    if not data.message.strip():
        from fastapi import HTTPException, status

        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="消息不能为空")

    return StreamingResponse(
        _stream_chat(data.message.strip()),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )
