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
    try:
        payload = await request.json()
    except Exception:
        # 非 JSON body 是确定性失败，400 让微信停止重试（而非 500 无限重试）
        raise HTTPException(status_code=400, detail="invalid json body")
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="invalid json body")
    if payload.get("action") == "CheckContainerPath":
        return PlainTextResponse("success")
    if settings.WECHAT_CLOUDRUN_PUBLIC_ACCESS and not request.headers.get("x-wx-source"):
        # 服务公网可达时只信任微信侧推送（官方「确认消息来源」带 x-wx-source 头）
        raise HTTPException(status_code=403, detail="untrusted source")
    if payload.get("Event") != "wxa_media_check":
        return PlainTextResponse("success")
    if payload.get("appid") != settings.WECHAT_APPID:
        raise HTTPException(status_code=403, detail="invalid appid")
    result = payload.get("result")
    if not isinstance(result, dict):
        result = {}
    errcode = payload.get("errcode")
    applied = await content_security_service.apply_media_result(
        db,
        str(payload.get("trace_id") or ""),
        str(result.get("suggest", "")),
        errcode if isinstance(errcode, int) else -1,
    )
    if not applied:
        # 提交检测与本地事务提交存在极短竞态；非 2xx 让微信稍后重试，
        # 避免首次回调早于 trace_id 入库时永久丢失审核结果。
        raise HTTPException(status_code=503, detail="moderation task not ready")
    return PlainTextResponse("success")
