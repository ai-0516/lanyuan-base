"""微信服务器事件回调。"""

import hashlib
import hmac

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.database import get_db
from app.services import content_security_service

router = APIRouter(prefix="/wechat/events", tags=["微信事件"])


def _valid_signature(signature: str, timestamp: str, nonce: str) -> bool:
    if not settings.WECHAT_MESSAGE_TOKEN:
        return False
    digest = hashlib.sha1(
        "".join(sorted([settings.WECHAT_MESSAGE_TOKEN, timestamp, nonce])).encode()
    ).hexdigest()
    return hmac.compare_digest(digest, signature)


def _verify(signature: str, timestamp: str, nonce: str) -> None:
    if not _valid_signature(signature, timestamp, nonce):
        raise HTTPException(status_code=403, detail="invalid signature")


@router.get("")
async def verify_wechat_server(
    signature: str = Query(...),
    timestamp: str = Query(...),
    nonce: str = Query(...),
    echostr: str = Query(...),
):
    """微信公众平台配置消息服务器时的 URL 验证。"""
    _verify(signature, timestamp, nonce)
    return PlainTextResponse(echostr)


@router.post("")
async def receive_wechat_event(
    request: Request,
    signature: str = Query(...),
    timestamp: str = Query(...),
    nonce: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    """接收明文 JSON 格式的 media_check_async 检测结果。"""
    _verify(signature, timestamp, nonce)
    payload = await request.json()
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
