"""认证相关 API"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.api.response import api_success
from app.services import auth_service

router = APIRouter(prefix="/auth", tags=["认证"])


@router.post("/login")
async def login(data: dict, db: AsyncSession = Depends(get_db)):
    """微信登录（开发环境模拟）"""
    code = data.get("code", "mock_code")
    result = await auth_service.login(db, code)
    return api_success(result)


@router.get("/check")
async def check_token(user_id: int = Depends(get_current_user)):
    """校验 Token 有效性

    Token 校验由 get_current_user Depends 完成：
    - 有效 → 返回 { valid: true, user_id }
    - 无效/过期 → 触发全局 401 异常
    """
    return api_success({"valid": True, "user_id": user_id})
