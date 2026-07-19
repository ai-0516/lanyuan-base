"""认证相关 API"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.services import auth_service
from app.schemas.user import LoginResponse

router = APIRouter(prefix="/auth", tags=["认证"])


@router.post("/login", response_model=LoginResponse)
async def login(data: dict, db: AsyncSession = Depends(get_db)):
    """微信登录（开发环境模拟）"""
    code = data.get("code", "mock_code")
    return await auth_service.login(db, code)


@router.get("/check")
async def check_token(user_id: int = Depends(get_current_user)):
    """检查 Token 是否有效"""
    return {"valid": True, "user_id": user_id}
