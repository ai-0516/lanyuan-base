"""认证相关 API"""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.api.response import api_success
from app.services import auth_service

router = APIRouter(prefix="/auth", tags=["认证"])


@router.post("/login")
async def login(data: dict, request: Request, db: AsyncSession = Depends(get_db)):
    """微信登录，获取微信昵称和头像

    openid 来源（二选一）：
    - 微信云托管 callContainer 链路：平台注入可信 x-wx-openid header
      （免 code2session，2026-09-04 路线2）→ 优先
    - 无 header（本地开发 wx.request）：code → code2session / mock
    """
    code = data.get("code", "mock_code")
    nickname = data.get("nickname")
    avatar = data.get("avatar")
    # 平台注入的 openid（云托管 callContainer）；外部直接请求（如公网 curl）
    # 无法伪造此 header——云托管网关会覆盖/丢弃客户端传入值
    wx_openid = (request.headers.get("x-wx-openid") or "").strip() or None
    result = await auth_service.login(db, code, nickname, avatar, openid=wx_openid)
    return api_success(result)


@router.get("/check")
async def check_token(user_id: int = Depends(get_current_user)):
    """校验 Token 有效性

    Token 校验由 get_current_user Depends 完成：
    - 有效 → 返回 { valid: true, user_id }
    - 无效/过期 → 触发全局 401 异常
    """
    return api_success({"valid": True, "user_id": user_id})
