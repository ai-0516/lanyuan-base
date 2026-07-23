"""用户认证业务逻辑"""

from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.core.security import create_access_token
from app.core.wechat import wechat_client
from app.schemas.user import LoginResponse, UserResponse


async def login(
    db: AsyncSession,
    code: str,
    nickname: Optional[str] = None,
    avatar: Optional[str] = None,
) -> LoginResponse:
    """微信登录：code 换 openid，查或创建用户，返回 JWT"""
    # 调微信 API 换 session
    session_info = await wechat_client.code2session(code)
    openid = session_info["openid"]

    # 查数据库
    result = await db.execute(select(User).where(User.openid == openid))
    user = result.scalar_one_or_none()

    if user is None:
        # 新用户自动注册（使用微信真实昵称/头像）
        user = User(
            openid=openid,
            nickname=nickname or "兰园业主",
            avatar=avatar or "",
        )
        db.add(user)
        await db.flush()
        # 回读 server_default 字段（created_at）
        await db.refresh(user)

    # 生成 JWT
    token = create_access_token(user.id)

    return LoginResponse(
        token=token,
        user=UserResponse.model_validate(user),
    )
