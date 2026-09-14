"""微信云托管消息推送回调。"""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.database import get_db
from app.services import content_security_service

router = APIRouter(prefix="/wechat/events", tags=["微信事件"])


@router.post("")
async def receive_wechat_event(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """接收云托管 JSON 消息，包括路径检测及 media_check_async 结果。"""
    payload = await request.json()
    if payload.get("action") == "CheckContainerPath":
        return PlainTextResponse("success")
    if payload.get("Event") != "wxa_media_check":
        return PlainTextResponse("success")
    if payload.get("appid") != settings.WECHAT_APPID:
        raise HTTPException(status_code=403, detail="invalid appid")
    result = payload.get("result") or {}
    applied = await content_security_service.apply_media_result(
        db,
        str(payload.get("trace_id", "")),
        str(result.get("suggest", "")),
        int(payload.get("errcode", -1)),
    )
    if not applied:
        # 提交检测与本地事务提交存在极短竞态；非 2xx 让微信稍后重试，
        # 避免首次回调早于 trace_id 入库时永久丢失审核结果。
        raise HTTPException(status_code=503, detail="moderation task not ready")
    return PlainTextResponse("success")
