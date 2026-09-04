"""认证相关 API"""

import re

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.api.response import api_error, api_success
from app.services import auth_service

router = APIRouter(prefix="/auth", tags=["认证"])

# 微信 openid 格式：真实 openid 为 ~28 位 [A-Za-z0-9_-]；上限 64 对齐 DB varchar(64)。
# 超长/非法字符在落库前拦截（防生产 MySQL DataError 500 与伪造注入）
_OPENID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


@router.post("/login")
async def login(data: dict, request: Request, db: AsyncSession = Depends(get_db)):
    """微信登录，获取微信昵称和头像

    openid 来源（二选一，2026-09-04 路线2）：
    - 微信云托管 callContainer / connectContainer 私有链路：平台注入可信
      x-wx-openid header（免 code2session，不调 api.weixin.qq.com）→ 优先。
      信任前提：服务公网访问已关闭（私有链路外部不可达；header 客户端无法伪造）
    - 无 header（本地开发 wx.request / 非云托管部署）：code → code2session / mock
    """
    code = data.get("code", "mock_code")
    nickname = data.get("nickname")
    avatar = data.get("avatar")
    # 平台注入的 openid 必为合法格式；出现非格式值 = 请求可疑（伪造/异常）
    # → 400 拒绝，不静默落入 code 路径
    wx_openid = None
    raw = (request.headers.get("x-wx-openid") or "").strip()
    if raw:
        if not _OPENID_RE.fullmatch(raw):
            api_error(40013, "登录参数错误，请重试")
        wx_openid = raw
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
