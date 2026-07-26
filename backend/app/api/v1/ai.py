"""AI 对话 API（SSE 流式）"""

import json

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.api.response import api_success
from app.schemas.ai import ChatRequest
from app.services import ai_service

router = APIRouter(prefix="/ai", tags=["AI 对话"])


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
        async for event, content in ai_service.stream_chat(
            db, user_id, data.session_id, data.message
        ):
            if event in ("token", "done", "error", "cmd_new_session"):
                yield f"event: {event}\ndata: {json.dumps(content, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
