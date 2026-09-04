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
    openid: Optional[str] = None,
) -> LoginResponse:
    """微信登录：查或创建用户，返回 JWT

    openid 两种来源：
    - openid 参数（云托管 x-wx-openid header，2026-09-04 路线2；**信任门控在 API 层**：
      auth.py 仅在 WX_TRUST_OPENID_HEADER=true 时解析 header 并做格式校验）
    - code 换 session（开发环境 / 传统链路）
    """
    # 平台注入 openid（云托管 callContainer）→ 跳过 code2session。
    # None/空串一律回退 code 路径（service 边界防御：空 openid 不落库，防唯一约束/DataError）
    if not openid:
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
    else:
        # 已有用户，更新登录时选择的昵称/头像
        if nickname is not None:
            user.nickname = nickname
        if avatar is not None:
            user.avatar = avatar
        db.add(user)
        await db.flush()

    # 生成 JWT
    token = create_access_token(user.id)

    return LoginResponse(
        token=token,
        user=UserResponse.model_validate(user),
    )
